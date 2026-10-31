# Lens catalog

Each lens is a **blind subagent**: it sees the resolved diff and its own contract,
never another lens's output. Executor preference is "specialized agent type if the
environment provides one → general-purpose agent given the lens prompt". Every lens
returns the finding contract in `convergence.md`. Lenses are **discovery only** —
they never fix, commit, or push.

| Lens key | Executor (preferred → fallback) | Scope | Contract / source |
|---|---|---|---|
| `code-review` | bundled `/code-review` skill → general-purpose | generic correctness + maintainability of the diff | bundled code-review |
| `bim-code-review` | `/bim-code-review` skill → general-purpose | repo-convention compliance + spec fidelity | `{repos}\BIMpossible\.claude\skills\bim-code-review` |
| `security-diff` | `security-engineer` → general-purpose | diff-scoped, reachable security risk | `security-diff-lens.md` |
| `revit-lifecycle` | general-purpose (+ revit-functionality-audit persona if present) | Revit API transaction / ExternalEvent / dispose lifecycle on changed C# | prompt below |
| `concurrency` | `qa-engineer` → general-purpose | async / threading / queue / race correctness on changed code | prompt below |

## code-review lens

Delegate to the bundled `/code-review` skill scoped to the resolved diff. Its own
10-angle pass and dedup run inside the subagent; it returns its ReportFindings
list. Map each entry into the finding contract with `lens: code-review` (carry its
category slug into `root_cause_key` where it helps dedup).

## bim-code-review lens

Delegate to `/bim-code-review` on the resolved fixed point. It runs its own blind
Standards + Spec subagents and returns both axes unmerged. Map Standards findings
to `lens: bim-code-review:standards` and Spec findings to
`lens: bim-code-review:spec` so convergence can dedup them against the other
lenses. **Runs only when the profile/heuristics declare BIM applicability.** Its
loud protected-file / unapproved-APS-write callout must survive convergence as a
retained finding, never dropped as noise.

## security-diff lens

See `security-diff-lens.md`. Prefer the `security-engineer` agent type.

## revit-lifecycle lens — prompt

> Review ONLY the supplied diff (plus the minimum surrounding code needed to judge
> reachability) for Revit API lifecycle correctness in changed C#. Flag only
> concrete, reachable defects:
> - Transaction / SubTransaction / TransactionGroup not committed or rolled back on
>   every path (exceptions, early returns included); nested-transaction misuse;
>   transaction started outside a valid document context.
> - Model mutation outside a transaction, or inside a read-only / ambient
>   transaction context.
> - ExternalEvent / IExternalEventHandler misuse: model mutation off the Revit API
>   context/thread; `Raise()` without a valid handler; assuming synchronous
>   completion.
> - IDisposable Revit objects (Transaction, collectors, Options, …) not disposed;
>   collectors enumerated after a document change.
> - ElementId / element references or UniqueId cached across a transaction or
>   document reload; stale version handling.
>
> Each finding: `file:line`, the failing path, the consequence (model corruption,
> unhandled exception, orphaned transaction), and a remediation direction. No style
> notes. Return the finding contract.

## concurrency lens — prompt

> Review ONLY the supplied diff (plus minimal surrounding context) for concurrency
> correctness on changed code. Flag only concrete, reachable defects:
> - Shared mutable state accessed without synchronization; check-then-act races;
>   non-atomic compound updates.
> - async/await misuse: dropped `await` (fire-and-forget swallowing errors),
>   sync-over-async deadlock, `async void`, ordering/ConfigureAwait pitfalls where
>   they matter.
> - Queue / worker / SignalR handlers assuming single-threaded or ordered delivery;
>   unbounded queues; lost cancellation.
> - Lock-scope shrink or lock-ordering inversion introduced by the diff; disposed or
>   captured context reused across threads.
>
> Each finding: `file:line`, the interleaving or path that triggers it, the
> consequence, and a remediation direction. Return the finding contract.
