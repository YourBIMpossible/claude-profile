---
name: revit-mcp-ambient-transaction-mutation
description: >-
  Write Revit element/parameter state through mcp-server-for-revit's
  send_code_to_revit, where the relay already owns an active transaction so a
  nested Transaction or TransactionGroup throws. Use whenever mutating a model
  via send_code_to_revit, or on "Transaction group cannot be started during an
  active transaction" / "starting a transaction from an external application ...
  is not allowed". NOT for production Add-In Commands, which own their execution
  context and use real TransactionGroups.
---

# Revit MCP ambient-transaction mutation

## Applies to
`mcp-server-for-revit` **injected C# execution only** (`send_code_to_revit`). The
relay wraps your code in the `Execute` method of a template that has **already
opened a Transaction** on the `document`.

**Does NOT apply to** a real BIMpossible Add-In `IExternalCommand`, which owns its
own Revit execution context. Those must use a real `TransactionGroup` + controlled
`Transaction`s + verification — see *Non-applicability* below. This skill is the
relay-probe / smoke-test path, not the shipped writer.

## Detection (confirm before assuming)
- `document.IsModifiable == true` **before your injected code runs anything** — a
  document is only modifiable inside an already-open transaction, so this alone
  proves the relay owns one.
- Any `new Transaction(document, ...).Start()` or
  `new TransactionGroup(document, ...).Start()` throws
  `InvalidOperationException: Transaction group cannot be started during an active
  transaction` (or the equivalent "cannot start a transaction" message).

If `IsModifiable == false`, you are NOT in ambient-transaction mode — you own the
scope and should open your own `Transaction`. Check first; do not guess.

## The rule (ordered — each step matters)
1. **Never** start a nested `Transaction` or `TransactionGroup` in relay mode.
   Write directly into the ambient transaction the relay already holds.
2. **Capture an exact before-state** for *every* parameter you may write **and
   every parameter you assert stays untouched** — by name → value. This is both
   the verification baseline and the restore payload.
3. **Apply only the pre-approved, scoped mutation.** Write the specific
   parameters in the plan and nothing else. Guard each: skip/abort if
   `LookupParameter` returns null or `IsReadOnly`.
4. **`document.Regenerate()`**, then **read back** the complete expected set
   (targets, peers, and protected params) with a fresh snapshot.
5. **Verify the full expected state**, all three clauses:
   - each target reaches its intended value;
   - each in-scope peer reaches its intended value;
   - every *other* captured boolean/param equals its before-value (no over-reach).
6. **On any exception or verification mismatch: compensating restore** — rewrite
   the captured before-values for everything you touched, `Regenerate()` again,
   then report failure with initial / attempted / observed states + diagnostics.
   (You cannot `RollBack` — the transaction isn't yours. Restoring before-values
   inside the ambient transaction makes the committed net state == original.)
7. **Confirm persistence with a fresh, independent relay call** that re-reads the
   parameters. `IsModifiable`/verification inside the mutating call is measured
   *before* the relay commits its ambient transaction; only a separate call proves
   the write actually persisted.

## Why compensating-restore, not RollBack
The relay commits its own transaction after your code returns (that is how relay
geometry/param writes persist at all). You have no handle to roll it back. So the
only way to guarantee "atomic on failure" is to leave the *values* in their
original state before returning on a failure path. Net committed state then equals
the before-state — same guarantee a `TransactionGroup.RollBack()` would give,
achieved through data instead of transaction control.

## Reference implementation (validated live, Revit 2025/2026)
Relay idioms baked in: the binding is `document` (not `doc`); `ElementId.Value`
returns `long` (`.IntegerValue` is removed in 2025/2026); detect Yes/No via
`p.Definition.GetDataType() == SpecTypeId.Boolean.YesNo`.

```csharp
// Scope: the exact parameter plan, pre-approved by the user.
var plan = new System.Collections.Generic.Dictionary<string,int> {
    {"Level 01",1},{"Level 02",0},{"Level 03",0} /* … */
};
string[] inScope = { "Level 01","Level 02","Level 03" /* … the writable set … */ };

var tb = document.GetElement(new ElementId((long)3177487));   // pin the instance by id
if (tb == null) return "ABORT: instance not found";

// Snapshot ALL relevant params by name (targets + peers + protected).
System.Func<System.Collections.Generic.Dictionary<string,int>> snap = () => {
    var d = new System.Collections.Generic.Dictionary<string,int>();
    foreach (Parameter p in tb.Parameters) {
        if (p == null || p.Definition == null || p.StorageType != StorageType.Integer) continue;
        bool yn; try { yn = p.Definition.GetDataType() == SpecTypeId.Boolean.YesNo; } catch { yn = false; }
        if (yn) d[p.Definition.Name] = p.AsInteger();
    }
    return d;
};

var before = snap();
if (!document.IsModifiable) return "ABORT: no ambient transaction (open your own)";

// Compensating restore for everything in scope.
System.Action restore = () => {
    foreach (var n in inScope) {
        var p = tb.LookupParameter(n);
        if (p != null && !p.IsReadOnly && before.ContainsKey(n)) p.Set(before[n]);
    }
    document.Regenerate();
};

var mism = new System.Collections.Generic.List<string>();
System.Collections.Generic.Dictionary<string,int> after = null;
try {
    foreach (var kv in plan) {
        var p = tb.LookupParameter(kv.Key);
        if (p == null || p.IsReadOnly) { restore(); return "ABORT_RESTORED: unwritable " + kv.Key; }
        p.Set(kv.Value);
    }
    document.Regenerate();
    after = snap();

    // (a) targets/peers reach plan values.
    foreach (var kv in plan) {
        int a; if (!after.TryGetValue(kv.Key, out a) || a != kv.Value)
            mism.Add(kv.Key + " expected " + kv.Value + " got " + (after.ContainsKey(kv.Key)?after[kv.Key].ToString():"MISSING"));
    }
    // (b) everything NOT in the plan is unchanged from before.
    var planned = new System.Collections.Generic.HashSet<string>(plan.Keys);
    foreach (var kv in before) {
        if (planned.Contains(kv.Key)) continue;
        int a; if (!after.TryGetValue(kv.Key, out a) || a != kv.Value)
            mism.Add("PROTECTED " + kv.Key + " changed " + kv.Value + " -> " + (after.ContainsKey(kv.Key)?after[kv.Key].ToString():"MISSING"));
    }

    if (mism.Count > 0) { restore(); return "VERIFY_FAILED_RESTORED: " + string.Join("; ", mism); }
} catch (System.Exception ex) {
    try { restore(); } catch {}
    return "EXCEPTION_RESTORED: " + ex.GetType().Name + ": " + ex.Message;
}
return "VERIFIED";   // relay commits the ambient transaction → then confirm with a SEPARATE read call
```

Then, in a **new** `send_code_to_revit` call, re-read the same parameter set and
confirm the values persisted. Only that independent read proves the commit.

## Non-applicability (do not misuse)
- **Not the production Add-In writer.** A shipped `IExternalCommand` owns its Revit
  context: use one outer `TransactionGroup`, short per-wave `Transaction`s,
  regenerate/read-back verification, and a genuine `RollBack`/`Assimilate` — never
  the compensating-restore shim, which exists only because the relay denies you
  transaction control.
- **Not for bulk/full-model sweeps** through the relay. Keep the scope to the
  explicitly selected, pre-approved elements; the relay times out on long spans.
- **Not a substitute for approval.** The mutation plan (exact writes, protected
  set) must be displayed and explicitly approved before the mutating call runs.
