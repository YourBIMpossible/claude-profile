"""Write an audit run's GRRP enrollment into its ledger (references/enrollment.md, Entry 3-4).

The ledger file itself is created by `grrp.py init`; this fills what init leaves blank and
what the gate requires, without weakening any gate rule:

  brief     fill the blank run-brief bullets (Scope, Boundaries, Done means, Authority, and
            Objective when blank). A bullet that already holds a different value is refused,
            never overwritten.
  criteria  write `grrp-criterion` blocks (AC-1 every check ran over its scope, AC-2 every
            finding is in the report and the ledger) from a JSON list.
  findings  write one `grrp-finding` per report finding from a JSON list, mapped per the
            enrollment table, plus one `grrp-transfer` for the open ones (no `ack`: an
            unaccepted transfer reports `transfers_ok: false`, the true result).

  batch     fill a batch manifest (`grrp.py batch-init`): the five Endpoint bullets, one
            `grrp-ledger` per run ledger, and the item inventory derived from each ledger's own
            `gate --json` output (gate item, transfer/deferral carrier, report artifact), plus
            `--end` to record the batch end. Items are upserted: status/evidence/check move,
            kind/text/source/covers are fixed (GRRP pins them). `owner_accepted` is never
            written here: only an owner can accept.

Every write is idempotent: a block whose id already exists with identical content is
skipped; the same id with different content is refused (exit 2). Statuses are recorded as
given; nothing here upgrades a HYPOTHESIS or marks a criterion met without evidence.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

BRIEF_KEYS = ("Objective", "Scope", "Boundaries", "Done means", "Extra findings", "Authority")
CRITERION_STATUSES = ("met", "unmet", "not_applicable", "accepted_exception")
LABELS = ("HYPOTHESIS", "CONFIRMED", "REFUTED", "INTENDED")
OBLIGATIONS = ("review", "integration", "deployment", "verification", "cleanup")
REVIEW_ONLY = "; ".join(f"{o}=n/a:review-only" for o in OBLIGATIONS)
FENCE = "`" * 3
BLOCK_RE = re.compile(rf"^{FENCE}grrp-(?P<kind>[a-z]+)\n(?P<body>.*?)^{FENCE}$", re.M | re.S)


class EnrollError(Exception):
    """Refused write; nothing was changed."""


def _one_line(v: object, key: str) -> str:
    s = str(v).strip()
    if not s or "\n" in s or FENCE in s:
        raise EnrollError(f"{key}: value must be one non-blank line without fences")
    if s.lower() in {"unknown", "?", "tbd"}:
        raise EnrollError(f"{key}: '{s}' is rejected by GRRP; record a value or 'n/a: <reason>'")
    return s


def fill_brief(text: str, values: dict[str, str]) -> str:
    out = text
    for key, val in values.items():
        if key not in BRIEF_KEYS:
            raise EnrollError(f"unknown brief key {key!r}")
        val = _one_line(val, key)
        m = re.search(rf"^- {re.escape(key)}:[ \t]*(.*)$", out, re.M)
        if not m:
            raise EnrollError(f"run brief has no '- {key}:' bullet (not a grrp init ledger?)")
        cur = m.group(1).strip()
        if cur == val:
            continue
        if cur:
            raise EnrollError(f"brief '{key}' already holds a different value: {cur!r}")
        out = out[:m.start()] + f"- {key}: {val}" + out[m.end():]
    return out


def render_block(kind: str, rec: dict[str, str]) -> str:
    return f"{FENCE}grrp-{kind}\n" + "".join(f"{k}: {v}\n" for k, v in rec.items()) + FENCE


def _existing(text: str, kind: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for m in BLOCK_RE.finditer(text):
        if m.group("kind") != kind:
            continue
        idm = re.search(r"^id:[ \t]*(\S+)", m.group("body"), re.M)
        if idm:
            found[idm.group(1)] = m.group(0)
    return found


def add_blocks(text: str, header: str, kind: str, recs: list[dict[str, str]]) -> str:
    have = _existing(text, kind)
    new: list[str] = []
    for rec in recs:
        block = render_block(kind, rec)
        if rec["id"] in have:
            if have[rec["id"]] != block:
                raise EnrollError(f"grrp-{kind} {rec['id']} already exists with different content")
            continue
        new.append(block)
    if not new:
        return text
    m = re.search(rf"^{re.escape(header)}[ \t]*$", text, re.M)
    if not m:
        raise EnrollError(f"ledger has no '{header}' section")
    nxt = re.search(r"^## ", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    section = text[m.end():end].rstrip("\n")
    body = (section + "\n\n" if section.strip() else "\n\n") + "\n\n".join(new) + "\n\n"
    return text[:m.end()] + body + text[end:]


def criteria_records(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not items:
        raise EnrollError("at least one criterion is required")
    out = []
    for it in items:
        cid = _one_line(it.get("id", ""), "criterion id")
        st = str(it.get("status", ""))
        if st not in CRITERION_STATUSES:
            raise EnrollError(f"{cid}: status {st!r} not in {CRITERION_STATUSES}")
        rec = {"id": cid, "text": _one_line(it.get("text", ""), f"{cid} text"), "status": st}
        if st == "met" or (st == "unmet" and it.get("evidence")):
            rec["evidence"] = _one_line(it.get("evidence", ""), f"{cid} evidence")
        elif st in ("not_applicable", "accepted_exception"):
            rec["reason"] = _one_line(it.get("reason", ""), f"{cid} reason")
        out.append(rec)
    if not any(r["status"] in ("met", "unmet") for r in out):
        raise EnrollError("a run must own at least one met/unmet criterion")
    return out


def finding_records(items: list[dict[str, Any]], requirement: str) -> list[dict[str, str]]:
    out = []
    for it in items:
        fid = _one_line(it.get("id", ""), "finding id")
        label = str(it.get("label", "")).upper()
        if label not in LABELS:
            raise EnrollError(f"{fid}: label {label!r} not in {LABELS}")
        evidence = str(it.get("evidence", "")).strip()
        rec: dict[str, str] = {"id": fid, "source": "discovered"}
        if label in ("REFUTED", "INTENDED"):
            rec.update(classification="original",
                       assessment="disproved" if label == "REFUTED" else "intentional_behavior",
                       resolution="resolved")
            evidence = _one_line(evidence, f"{fid} evidence ({label} needs the why)")
        else:
            rec.update(classification="independent",
                       assessment="unassessed" if label == "HYPOTHESIS" else "confirmed_defect",
                       resolution="open")
        rec.update(execution_state="identified",
                   owner=_one_line(it.get("owner", "parent"), f"{fid} owner"),
                   lane="parent",
                   requirement=_one_line(it.get("requirement", requirement), f"{fid} requirement"),
                   authority="authorized" if rec["resolution"] == "resolved" else "approval_required",
                   blocks=str(it.get("blocks") or "none"),
                   evidence=_one_line(evidence, f"{fid} evidence") if evidence else "none")
        if rec["resolution"] == "resolved":
            rec["obligations"] = REVIEW_ONLY
        for opt in ("severity", "location", "summary"):
            if it.get(opt):
                rec[opt] = _one_line(it[opt], f"{fid} {opt}")
        out.append(rec)
    return out


def transfer_record(findings: list[dict[str, str]], tid: str, receiver: str, next_action: str,
                    acceptance: str, follow_up: str) -> dict[str, str] | None:
    open_ids = [f["id"] for f in findings if f["resolution"] == "open"]
    if not open_ids:
        return None
    return {"id": tid, "findings": ", ".join(open_ids), "receiver": _one_line(receiver, "receiver"),
            "next_action": _one_line(next_action, "next_action"),
            "acceptance": _one_line(acceptance, "acceptance"), "follow_up": _one_line(follow_up, "follow_up")}


ENDPOINT_KEYS = ("Runs", "Follow-through", "Authority", "Authority gaps", "Archive evidence")
ITEM_KINDS = {"criterion", "obligation", "owner_ask", "left_flag", "artifact", "delivery", "integration",
              "activation", "behavior", "mirror", "alert", "decision", "instruction"}
ITEM_STATUSES = {"complete", "duplicate", "superseded", "inapplicable", "open", "in_progress",
                 "transferred", "deferred", "blocked_owner"}
ITEM_PINNED = ("kind", "text", "source", "covers")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def fill_endpoint(text: str, values: dict[str, str]) -> str:
    m = re.search(r"^## Endpoint[ \t]*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        raise EnrollError("manifest has no '## Endpoint' section (not a grrp batch-init manifest?)")
    sec = m.group(1)
    for key, val in values.items():
        val = _one_line(val, key)
        if val.lower() == "none" and key != "Authority gaps":
            raise EnrollError(f"endpoint '{key}': 'none' is allowed only for Authority gaps")
        bm = re.search(rf"^- {re.escape(key)}:[ \t]*(.*)$", sec, re.M)
        if not bm:
            raise EnrollError(f"endpoint has no '- {key}:' bullet")
        cur = bm.group(1).strip()
        if cur == val:
            continue
        if cur and cur.lower() != "tbd":
            raise EnrollError(f"endpoint '{key}' already holds a different value: {cur!r} (amend through the owner)")
        sec = sec[:bm.start()] + f"- {key}: {val}" + sec[bm.end():]
    return text[:m.start(1)] + sec + text[m.end(1):]


def _fields(block: str) -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"^([a-z_]+):[ \t]*(.*)$", block, re.M)}


def upsert_items(text: str, recs: list[dict[str, str]]) -> str:
    """Add new items; replace an existing item only when its pinned fields are unchanged."""
    have = _existing(text, "item")
    new: list[dict[str, str]] = []
    for rec in recs:
        old = have.get(rec["id"])
        if old is None:
            new.append(rec)
            continue
        of = _fields(old)
        changed = [k for k in ITEM_PINNED if of.get(k, "") != rec.get(k, "")]
        if changed:
            raise EnrollError(f"grrp-item {rec['id']}: {', '.join(changed)} changed; a different "
                              "obligation is a new item that supersedes this one")
        text = text.replace(old, render_block("item", rec), 1)
    return add_blocks(text, "## Items", "item", new)


def item_records(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for it in items:
        iid = _one_line(it.get("id", ""), "item id")
        if not SAFE_ID_RE.fullmatch(iid):
            raise EnrollError(f"item id {iid!r}: 1-64 of [A-Za-z0-9._-]")
        st = str(it.get("status", ""))
        if st == "owner_accepted":
            raise EnrollError(f"{iid}: owner_accepted is the owner's act, never written by enrollment")
        if st not in ITEM_STATUSES:
            raise EnrollError(f"{iid}: status {st!r} not in {sorted(ITEM_STATUSES)}")
        kind = str(it.get("kind", ""))
        if kind not in ITEM_KINDS:
            raise EnrollError(f"{iid}: kind {kind!r} not in {sorted(ITEM_KINDS)}")
        rec = {"id": iid, "kind": kind}
        for k in ("text", "source", "owner", "action"):
            rec[k] = _one_line(it.get(k, ""), f"{iid} {k}")
        rec["status"] = st
        rec["evidence"] = _one_line(it.get("evidence") or "none", f"{iid} evidence")
        if st not in ("open", "in_progress") and rec["evidence"] == "none":
            raise EnrollError(f"{iid}: status {st} needs evidence")
        for opt in ("check", "covers", "replacement", "reviewed", "owner_action"):
            if it.get(opt):
                rec[opt] = _one_line(it[opt], f"{iid} {opt}")
        out.append(rec)
    return out


def derive_items(lid: str, gate: dict[str, Any], report: str | None, receiver: str,
                 next_action: str) -> list[dict[str, Any]]:
    """Inventory one run ledger from its own gate verdict; nothing is cleared that the gate did not clear."""
    verdict = str(gate.get("verdict", ""))
    if verdict not in ("complete", "incomplete", "blocked", "invalid"):
        raise EnrollError(f"{lid}: gate JSON has no verdict")
    reasons = (list(gate.get("incomplete", []))[:6] + list(gate.get("blocked", []))[:3]
               + [f"validation: {e}" for e in list(gate.get("validation_errors", []))[:3]])
    why = "; ".join(str(r) for r in reasons) or "none"
    items: list[dict[str, Any]] = [{
        "id": f"{lid}-gate", "kind": "criterion", "text": f"run ledger {lid} gate complete",
        "source": f"ledger {lid}", "owner": "parent", "action": "resolve the ledger's open criteria and findings",
        "status": "complete" if verdict == "complete" else "open",
        "evidence": f"gate verdict {verdict}" + ("" if verdict == "complete" else f": {why}"),
        "check": f"ledger {lid} complete"}]
    refs = [str(x).split(":")[0] for x in gate.get("transfers_unacknowledged", [])]
    refs += [str(x).split(":")[0] for x in gate.get("deferred", [])]
    refs += [str(x) for x in gate.get("portfolio_open", [])]
    refs = list(dict.fromkeys(r.strip() for r in refs if r.strip()))
    if refs:
        items.append({"id": f"{lid}-carry", "kind": "obligation",
                      "text": f"open findings and transfers of ledger {lid}", "source": f"ledger {lid}",
                      "owner": receiver, "action": next_action, "status": "transferred",
                      "evidence": f"transferred, not acknowledged: {', '.join(refs)}",
                      "covers": ", ".join(f"{lid}:{r}" for r in refs)})
    if report:
        ok = Path(report).is_file()
        items.append({"id": f"{lid}-report", "kind": "artifact", "text": f"report of run ledger {lid}",
                      "source": f"ledger {lid}", "owner": "parent", "action": "write the report",
                      "status": "complete" if ok else "open",
                      "evidence": report if ok else "none", "check": f"file {report}"})
    return items


def set_end(text: str, end: str) -> str:
    m = re.search(r"^end:[ \t]*(.*)$", text, re.M)
    if not m:
        raise EnrollError("manifest has no metrics 'end:' line")
    cur = m.group(1).strip()
    if cur == end:
        return text
    if cur and cur != "none":
        raise EnrollError(f"metrics end already recorded: {cur}")
    return text[:m.start()] + f"end: {end}" + text[m.end():]


def _load(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise EnrollError(f"{path}: expected a JSON list")
    return data


def _write(path: Path, before: str, after: str) -> None:
    if after != before:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(after, encoding="utf-8", newline="\n")
        tmp.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief")
    b.add_argument("--ledger", required=True)
    for key in BRIEF_KEYS:
        b.add_argument("--" + key.lower().replace(" ", "-"))
    c = sub.add_parser("criteria")
    c.add_argument("--ledger", required=True)
    c.add_argument("--criteria-json", required=True)
    f = sub.add_parser("findings")
    f.add_argument("--ledger", required=True)
    f.add_argument("--findings-json", required=True)
    f.add_argument("--requirement", default="AC-2")
    f.add_argument("--transfer-id", default="T-1")
    f.add_argument("--receiver")
    f.add_argument("--next-action")
    f.add_argument("--acceptance")
    f.add_argument("--follow-up")
    bt = sub.add_parser("batch")
    bt.add_argument("--manifest", required=True)
    for key in ENDPOINT_KEYS:
        bt.add_argument("--" + key.lower().replace(" ", "-"), dest="ep_" + key.lower().replace(" ", "_").replace("-", "_"))
    bt.add_argument("--add-ledger", action="append", default=[], metavar="ID=PATH")
    bt.add_argument("--gate", action="append", default=[], metavar="ID=GATE_JSON",
                    help="derive that ledger's items from its own `grrp.py gate --json` output")
    bt.add_argument("--report", action="append", default=[], metavar="ID=REPORT")
    bt.add_argument("--receiver", default="owner")
    bt.add_argument("--next-action", default="verify each open finding")
    bt.add_argument("--items-json")
    bt.add_argument("--end", help="ISO datetime with zone, or 'now' (UTC)")
    a = ap.parse_args(argv)
    if a.cmd == "batch":
        return _batch(a)
    path = Path(a.ledger)
    try:
        text = path.read_text(encoding="utf-8")
        if not re.search(r"^grrp: v1[ \t]*$", text, re.M):
            raise EnrollError(f"{path}: not a GRRP v1 ledger")
        if a.cmd == "brief":
            vals = {k: getattr(a, k.lower().replace(" ", "_")) for k in BRIEF_KEYS}
            new = fill_brief(text, {k: v for k, v in vals.items() if v is not None})
        elif a.cmd == "criteria":
            new = add_blocks(text, "## Acceptance criteria", "criterion", criteria_records(_load(a.criteria_json)))
        else:
            recs = finding_records(_load(a.findings_json), a.requirement)
            new = add_blocks(text, "## Findings", "finding", recs)
            if any(r["resolution"] == "open" for r in recs):
                missing = [n for n in ("receiver", "next_action", "acceptance", "follow_up") if not getattr(a, n)]
                if missing:
                    raise EnrollError("open findings need a transfer: "
                                      + ", ".join("--" + m.replace("_", "-") for m in missing))
                t = transfer_record(recs, a.transfer_id, a.receiver, a.next_action, a.acceptance, a.follow_up)
                new = add_blocks(new, "## Transfers", "transfer", [t] if t else [])
    except (EnrollError, OSError, json.JSONDecodeError) as exc:
        print(f"grrp_enroll: {exc}", file=sys.stderr)
        return 2
    _write(path, text, new)
    print(json.dumps({"ledger": str(path), "changed": new != text}))
    return 0


def _pairs(vals: list[str], flag: str) -> dict[str, str]:
    out = {}
    for v in vals:
        k, sep, rest = v.partition("=")
        if not sep or not k or not rest:
            raise EnrollError(f"{flag} wants ID=VALUE, got {v!r}")
        out[k] = rest
    return out


def _batch(a: argparse.Namespace) -> int:
    path = Path(a.manifest)
    try:
        text = path.read_text(encoding="utf-8")
        if not re.search(r"^grrp-batch: v1[ \t]*$", text, re.M):
            raise EnrollError(f"{path}: not a GRRP batch manifest")
        ep = {k: getattr(a, "ep_" + k.lower().replace(" ", "_").replace("-", "_")) for k in ENDPOINT_KEYS}
        new = fill_endpoint(text, {k: v for k, v in ep.items() if v is not None})
        ledgers = _pairs(a.add_ledger, "--add-ledger")
        for lid, lp in ledgers.items():
            if not SAFE_ID_RE.fullmatch(lid) or not Path(lp).is_file():
                raise EnrollError(f"--add-ledger {lid}: bad id or missing file {lp}")
        new = add_blocks(new, "## Ledgers", "ledger", [{"id": k, "path": v} for k, v in ledgers.items()])
        reports = _pairs(a.report, "--report")
        derived: list[dict[str, Any]] = []
        for lid, gp in _pairs(a.gate, "--gate").items():
            if lid not in _existing(new, "ledger"):
                raise EnrollError(f"--gate {lid}: no grrp-ledger with that id")
            derived += derive_items(lid, json.loads(Path(gp).read_text(encoding="utf-8-sig")),
                                    reports.get(lid), a.receiver, a.next_action)
        if a.items_json:
            derived += _load(a.items_json)
        if derived:
            new = upsert_items(new, item_records(derived))
        cur_end = re.search(r"^end:[ \t]*(.*)$", new, re.M)
        ended = bool(cur_end and cur_end.group(1).strip() not in ("", "none"))
        if a.end and not (a.end == "now" and ended):  # `now` on a re-run keeps the recorded end
            end = (_dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
                   .replace("+00:00", "Z")) if a.end == "now" else a.end
            new = set_end(new, _one_line(end, "end"))
    except (EnrollError, OSError, json.JSONDecodeError) as exc:
        print(f"grrp_enroll: {exc}", file=sys.stderr)
        return 2
    _write(path, text, new)
    print(json.dumps({"manifest": str(path), "changed": new != text}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
