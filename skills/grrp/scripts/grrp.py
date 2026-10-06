#!/usr/bin/env python3
"""GRRP v1 ledger tool: create, check, gate, and summarize a Markdown run ledger.

The ledger is one Markdown file written only by the parent session. Structured
records are fenced blocks (```grrp-criterion / grrp-finding / grrp-child /
grrp-transfer) of flat `key: value` lines, so any model or human can read and
write them without a YAML dependency. Format: ../references/ledger-format.md.

Commands:
  init   --slug S [--dir D] [--objective TEXT]   create the ledger, or return the existing one
  status LEDGER                                  resume view: checkpoint, open items, known ids
  gate   LEDGER [--json]                         completion verdict
  metrics LEDGER                                 pilot measurements

Gate exit codes: 0 complete, 1 incomplete, 2 blocked, 3 invalid ledger.
Stdlib only; Python 3.10+.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DIR = Path(os.environ.get("GRRP_LEDGER_DIR", r"F:\Claude-Tools\reports\grrp"))

DEFAULT_QUEUE = Path(os.environ.get("GRRP_QUEUE", r"F:\Claude-Tools\state\queue.yaml"))
# /next statuses meaning the queue has taken responsibility (parked/dropped = refused).
QUEUE_ACCEPTING = {"ready", "blocked_dep", "blocked_external", "blocked_owner", "in_flight", "landed", "live"}

CLASSIFICATIONS = {"original", "blocker", "independent", "enhancement", "externally_owned"}
ASSESSMENTS = {"unassessed", "confirmed_defect", "intentional_behavior", "disproved"}
RESOLUTIONS = {"open", "resolved", "accepted_exception"}
EXEC_STATES = [
    "identified", "implementing", "implemented", "reviewed",
    "integrated", "deployed", "verified", "cleanup_complete",
]
AUTHORITIES = {"authorized", "approval_required", "protected"}
CRITERION_STATUSES = {"met", "unmet", "not_applicable", "accepted_exception"}
OBLIGATIONS = ["review", "integration", "deployment", "verification", "cleanup"]
# execution_state at or past this index claims the obligation was performed.
OBLIGATION_STATE = {
    "review": "reviewed", "integration": "integrated", "deployment": "deployed",
    "verification": "verified", "cleanup": "cleanup_complete",
}
NONE_VALUES = {"", "none", "-", "null"}
UNKNOWN_VALUES = {"unknown", "?", "tbd"}
BRIEF_KEYS = ["Objective", "Scope", "Boundaries", "Done means", "Extra findings", "Authority"]

BLOCK_OPEN_RE = re.compile(r"^```grrp-(criterion|finding|child|transfer)$")
BRIEF_RE = re.compile(r"^- (Objective|Scope|Boundaries|Done means|Extra findings|Authority):[ \t]*(.*)$", re.M)


@dataclass
class Ledger:
    path: Path
    text: str
    brief: dict[str, str] = field(default_factory=dict)
    blocks: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def of(self, kind: str) -> list[dict[str, str]]:
        return self.blocks.get(kind, [])


def _blank(v: str | None) -> bool:
    return v is None or v.strip().lower() in NONE_VALUES


def _list(v: str | None) -> list[str]:
    if _blank(v):
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def _brief_section(text: str) -> str:
    m = re.search(r"^## Run brief[ \t]*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def parse(path: Path) -> Ledger:
    """Line state machine: any line mentioning a ```grrp fence must be an exact, closed block."""
    text = path.read_text(encoding="utf-8")
    led = Ledger(path=path, text=text)
    led.brief = {k: v.strip() for k, v in BRIEF_RE.findall(_brief_section(text))}
    kind: str | None = None
    rec: dict[str, str] = {}
    start = 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if kind is None:
            m = BLOCK_OPEN_RE.match(line)
            if m:
                kind, rec, start = m.group(1), {}, n
            elif "```grrp" in line:
                led.errors.append(f"line {n}: malformed grrp fence {line.strip()!r} (exact '```grrp-<kind>' at column 0)")
            continue
        if line == "```":
            led.blocks.setdefault(kind, []).append(rec)
            kind = None
            continue
        if "```" in line:
            led.errors.append(f"line {n}: fence inside grrp-{kind} block opened at line {start}")
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            led.errors.append(f"grrp-{kind} block: line {n} is not 'key: value': {line.strip()!r}")
            continue
        k, v = (s.strip() for s in line.split(":", 1))
        if k in rec:
            led.errors.append(f"grrp-{kind} block line {n}: key '{k}' repeated")
        rec[k] = v
    if kind is not None:
        led.errors.append(f"grrp-{kind} block opened at line {start} is never closed")
    return led


def _obligations(rec: dict[str, str]) -> dict[str, tuple[str, str]]:
    """`review=done:receipt-3; deployment=n/a:review-only` -> {name: (state, detail)}; ';' only."""
    out: dict[str, tuple[str, str]] = {}
    for part in [p.strip() for p in rec.get("obligations", "").split(";") if p.strip() and not _blank(p)]:
        name, _, rest = part.partition("=")
        state, _, detail = rest.partition(":")
        out[name.strip()] = (state.strip(), detail.strip())
    return out


def validate(led: Ledger) -> list[str]:
    errs = list(led.errors)
    if not re.search(r"^grrp: v1[ \t]*$", led.text, re.M):
        errs.append("missing 'grrp: v1' header line")
    for key in BRIEF_KEYS:
        if _blank(led.brief.get(key)):
            errs.append(f"run brief: '{key}' is empty")

    ids: set[str] = set()
    crit_ids = {c.get("id", "") for c in led.of("criterion")}
    child_ids = {c.get("id", "") for c in led.of("child")}
    for kind in ("criterion", "finding", "child", "transfer"):
        for rec in led.of(kind):
            rid = rec.get("id", "")
            if _blank(rid):
                errs.append(f"grrp-{kind} block without id")
            elif rid in ids:
                errs.append(f"{rid}: duplicate id")
            ids.add(rid)
            for k, v in rec.items():
                if v.strip().lower() in UNKNOWN_VALUES:
                    errs.append(f"{rid}: '{k}' is '{v}' — record a value or 'n/a: <reason>'")

    if not led.of("criterion"):
        errs.append("no acceptance criteria (grrp-criterion blocks)")
    elif not any(c.get("status") in ("met", "unmet") for c in led.of("criterion")):
        errs.append("every criterion is n/a or exception — a run must own at least one real criterion")
    for c in led.of("criterion"):
        cid, st = c.get("id", "?"), c.get("status", "")
        if st not in CRITERION_STATUSES:
            errs.append(f"{cid}: status '{st}' not in {sorted(CRITERION_STATUSES)}")
        if _blank(c.get("text")):
            errs.append(f"{cid}: empty text")
        if st == "met" and _blank(c.get("evidence")):
            errs.append(f"{cid}: met without evidence")
        if st in {"not_applicable", "accepted_exception"} and _blank(c.get("reason")):
            errs.append(f"{cid}: {st} requires reason (exception: the authorizing decision)")

    for f in led.of("finding"):
        fid = f.get("id", "?")
        for key, allowed in (("classification", CLASSIFICATIONS), ("assessment", ASSESSMENTS),
                             ("resolution", RESOLUTIONS), ("authority", AUTHORITIES),
                             ("execution_state", set(EXEC_STATES))):
            if f.get(key, "") not in allowed:
                errs.append(f"{fid}: {key} '{f.get(key, '')}' not in {sorted(allowed)}")
        for key in ("source", "owner", "requirement"):
            if _blank(f.get(key)):
                errs.append(f"{fid}: '{key}' must never be empty")
        req = f.get("requirement", "")
        if not _blank(req) and req not in crit_ids and not req.startswith("n/a"):
            errs.append(f"{fid}: requirement '{req}' is not a criterion id (or 'n/a: <reason>')")
        if f.get("classification") == "blocker" and not _list(f.get("blocks")):
            errs.append(f"{fid}: classification blocker must name the criteria it blocks")
        for b in _list(f.get("blocks")):
            if b not in crit_ids:
                errs.append(f"{fid}: blocks unknown criterion '{b}'")
        lane = f.get("lane", "parent")
        if lane not in {"parent", "external"} and lane not in child_ids:
            errs.append(f"{fid}: lane '{lane}' is not parent/external/a child id")

        assess, res = f.get("assessment"), f.get("resolution")
        if assess in {"disproved", "intentional_behavior"} and _blank(f.get("evidence")):
            errs.append(f"{fid}: {assess} requires evidence")
        if res == "accepted_exception" and _blank(f.get("reason")):
            errs.append(f"{fid}: accepted_exception requires reason (the authorizing decision)")
        if res == "resolved" and _blank(f.get("evidence")):
            errs.append(f"{fid}: resolved without closure evidence")

        state = f.get("execution_state", "identified")
        started = state in EXEC_STATES and EXEC_STATES.index(state) > 0
        if started and f.get("authority") == "protected":
            errs.append(f"{fid}: protected surface shows execution '{state}' — unauthorized action")
        if started and f.get("classification") == "enhancement" and _blank(f.get("scope_approval")):
            errs.append(f"{fid}: enhancement executed without scope_approval")
        if started and f.get("authority") == "approval_required" and _blank(f.get("action_approval")):
            errs.append(f"{fid}: approval_required action executed without action_approval")

        obl = _obligations(f)
        for name, (ost, detail) in obl.items():
            if name not in OBLIGATIONS:
                errs.append(f"{fid}: unknown obligation '{name}'")
            elif ost not in {"done", "pending", "n/a"}:
                errs.append(f"{fid}: obligation {name} state '{ost}' not done/pending/n/a")
            elif ost in {"done", "n/a"} and not detail:
                errs.append(f"{fid}: obligation {name}={ost} needs evidence or reason after ':'")
        # Evidence-state truth: a progress label never claims an unproven obligation.
        if state in EXEC_STATES:
            for name, needed in OBLIGATION_STATE.items():
                if EXEC_STATES.index(state) >= EXEC_STATES.index(needed):
                    ost = obl.get(name, ("", ""))[0]
                    if ost not in {"done", "n/a"}:
                        errs.append(f"{fid}: execution_state '{state}' claims {name} but obligation is '{ost or 'missing'}'")

    for c in led.of("child"):
        cid = c.get("id", "?")
        for key in ("objective", "exclusions", "acceptance", "reserved", "return_evidence", "owner", "location"):
            if _blank(c.get(key)):
                errs.append(f"{cid}: child contract '{key}' is empty (use 'n/a: <reason>')")
        if c.get("may_delegate", "false") != "true" and not _blank(c.get("spawned")):
            errs.append(f"{cid}: spawned sub-lanes without may_delegate: true")

    for t in led.of("transfer"):
        tid = t.get("id", "?")
        for key in ("findings", "receiver", "next_action", "acceptance", "follow_up"):
            if _blank(t.get(key)):
                errs.append(f"{tid}: transfer '{key}' is empty")
    return errs


def transfer_acceptance(t: dict[str, str], ledger_path: Path, queue: Path | None) -> str | None:
    """None when the receiver has accepted responsibility, else the reason it has not.

    `receiver: queue:<ID>` is accepted by the /next store itself: the item exists, carries an
    accepting status, and references this ledger (so it holds the transfer's context).
    Any other receiver is accepted only by an `ack:` naming the executor's receipt.
    """
    recv = t.get("receiver", "")
    if recv.startswith("queue:"):
        qid = recv[len("queue:"):].strip()
        if queue is None or not queue.is_file():
            return f"queue store not readable ({queue}); cannot confirm {qid}"
        text = queue.read_text(encoding="utf-8")
        m = re.search(rf"^- id: {re.escape(qid)}\s*$(.*?)(?=^- id: |\Z)", text, re.M | re.S)
        if not m:
            return f"{qid} not in queue store — entry never consumed"
        sts = re.findall(r"^  status:[ \t]*(\S+)", m.group(1), re.M)
        if len(sts) != 1:
            return f"{qid} has {len(sts)} item-level status keys; cannot confirm acceptance"
        status = sts[0]
        if status not in QUEUE_ACCEPTING:
            return f"{qid} status '{status}' does not accept responsibility"
        if not re.search(rf"(?<![\w.-]){re.escape(ledger_path.name)}(?![\w.-])", m.group(1)):
            return f"{qid} does not reference {ledger_path.name}; transfer context not carried"
        return None
    if _blank(t.get("ack")):
        return f"receiver '{recv}' has not acknowledged (no ack receipt)"
    return None


def gate(led: Ledger, queue: Path | None = DEFAULT_QUEUE) -> dict:
    errs = validate(led)
    findings, crits = led.of("finding"), led.of("criterion")
    children = {c.get("id"): c for c in led.of("child")}
    transferred = {fid for t in led.of("transfer") for fid in _list(t.get("findings"))}
    blocked: list[str] = []
    incomplete: list[str] = []

    for c in crits:
        if c.get("status") == "unmet":
            incomplete.append(f"{c.get('id')}: criterion unmet")
    for f in findings:
        fid = f.get("id", "?")
        if f.get("assessment") == "unassessed":
            incomplete.append(f"{fid}: not assessed")
        blocks = _list(f.get("blocks"))
        if blocks and f.get("resolution") == "open":
            why = "protected surface" if f.get("authority") == "protected" else f"lane {f.get('lane', 'parent')}"
            blocked.append(f"{fid}: open, blocks {', '.join(blocks)} ({why})")
        if f.get("resolution") == "resolved":
            for name, (ost, _) in _obligations(f).items():
                if ost == "pending":
                    incomplete.append(f"{fid}: {name} obligation pending")
        cls = f.get("classification")
        if f.get("resolution") == "open" and not blocks:
            if cls in {"original"}:
                incomplete.append(f"{fid}: original finding still open")
            elif cls in {"independent", "externally_owned"} and fid not in transferred:
                incomplete.append(f"{fid}: {cls} finding has no transfer record")
    for cid, c in children.items():
        blocking = [f.get("id") for f in findings if f.get("lane") == cid and _list(f.get("blocks"))]
        if blocking and _blank(c.get("receipt")):
            blocked.append(f"{cid}: blocking child has no return receipt")

    pending_transfers = []
    for t in led.of("transfer"):
        why = transfer_acceptance(t, led.path, queue)
        if why:
            pending_transfers.append(f"{t.get('id')}: {why}")
    if errs:
        verdict = "invalid"
    elif blocked:
        verdict = "blocked"
    elif incomplete:
        verdict = "incomplete"
    else:
        verdict = "complete"
    return {
        "ledger": str(led.path), "verdict": verdict, "validation_errors": errs,
        "blocked": blocked, "incomplete": incomplete,
        "transfers_unacknowledged": pending_transfers,
        "transfers_ok": not pending_transfers,
        "portfolio_open": sorted(transferred & {f.get("id") for f in findings if f.get("resolution") == "open"}),
    }


EXIT = {"complete": 0, "incomplete": 1, "blocked": 2, "invalid": 3}

TEMPLATE = """# GRRP ledger — {slug}

grrp: v1
created: {date}
writer: parent session only — children return receipts, never edit this file

## Run brief
- Objective: {objective}
- Scope:
- Boundaries:
- Done means:
- Extra findings: classify original|blocker|independent|enhancement|externally_owned; non-blocking extras route to a transfer, never absorbed
- Authority:

## Change log
- {date}: ledger created

## Acceptance criteria

## Findings

## Children

## Transfers

## Checkpoint
- Next action:
- Revisions:
- Running/temporary state: none
"""


def cmd_init(a: argparse.Namespace) -> int:
    d = Path(a.dir) if a.dir else DEFAULT_DIR
    existing = sorted(d.glob(f"*-{a.slug}.ledger.md")) if d.exists() else []
    if existing:
        print(f"existing: {existing[-1]}")
        return cmd_status(argparse.Namespace(ledger=str(existing[-1])))
    d.mkdir(parents=True, exist_ok=True)
    date = _dt.date.today().isoformat()
    p = d / f"{date}-{a.slug}.ledger.md"
    p.write_text(TEMPLATE.format(slug=a.slug, date=date, objective=a.objective or ""), encoding="utf-8")
    print(f"created: {p}")
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    led = parse(Path(a.ledger))
    m = re.search(r"^## Checkpoint\n(.*?)(?=^## |\Z)", led.text, re.M | re.S)
    print("checkpoint:\n" + (m.group(1).strip() if m else "(none)"))
    known = [f"{f.get('id')}[{f.get('classification')}/{f.get('resolution')}/{f.get('execution_state')}]"
             for f in led.of("finding")]
    print("known findings (do not rediscover): " + (", ".join(known) or "none"))
    print("open criteria: " + (", ".join(c.get("id", "?") for c in led.of("criterion") if c.get("status") == "unmet") or "none"))
    print("children without receipt: " + (", ".join(c.get("id", "?") for c in led.of("child") if _blank(c.get("receipt"))) or "none"))
    return 0


def cmd_gate(a: argparse.Namespace) -> int:
    r = gate(parse(Path(a.ledger)), Path(a.queue) if a.queue else DEFAULT_QUEUE)
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"verdict: {r['verdict']}")
        print(f"transfers: {'all accepted' if r['transfers_ok'] else 'OUTSTANDING (reported separately; not a parent blocker)'}")
        for k in ("validation_errors", "blocked", "incomplete", "transfers_unacknowledged", "portfolio_open"):
            if r[k]:
                print(f"{k}:")
                for x in r[k]:
                    print(f"  - {x}")
    return EXIT[r["verdict"]]


def metrics(led: Ledger) -> dict:
    r = gate(led)
    fs = led.of("finding")
    return {
        "findings": len(fs),
        "original_findings": sum(f.get("source") == "original" for f in fs),
        "lost_findings": sum(f.get("assessment") == "unassessed" for f in fs),
        "duplicates_linked": sum(not _blank(f.get("duplicate_of")) for f in fs),
        "evidence_state_errors": sum("claims" in e or "without" in e for e in r["validation_errors"]),
        "failed_transfers": len(r["transfers_unacknowledged"]),
        "scope_changes": max(0, len(re.findall(r"^- \d{4}-\d{2}-\d{2}", led.text.split("## Change log", 1)[-1].split("##", 1)[0], re.M)) - 1),
        "owner_interruptions": len(re.findall(r"owner-asked", led.text)),
        "verdict": r["verdict"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="grrp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--slug", required=True); p.add_argument("--dir"); p.add_argument("--objective")
    p = sub.add_parser("status"); p.add_argument("ledger")
    p = sub.add_parser("gate"); p.add_argument("ledger"); p.add_argument("--json", action="store_true"); p.add_argument("--queue")
    p = sub.add_parser("metrics"); p.add_argument("ledger")
    a = ap.parse_args(argv)
    if a.cmd == "init":
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", a.slug):
            ap.error("slug must be lowercase kebab-case")
        return cmd_init(a)
    if a.cmd != "init" and not Path(a.ledger).is_file():
        print(f"error: ledger not found: {a.ledger}", file=sys.stderr)
        return 3
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "gate":
        return cmd_gate(a)
    print(json.dumps(metrics(parse(Path(a.ledger))), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
