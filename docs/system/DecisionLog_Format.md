# Decision Log Format Reference
**Purpose:** Defines the standard format for daily decision log entries. Upload this with the project so any Claude session can match the convention without being asked.

---

## File naming

- One file per day, only on days where decisions were actually made
- Format: `YYYY-MM-DD.md` (e.g. `2026-05-09.md`)
- Stored in the daily decision log folder (location varies by project — usually `decision-log/` or `01_BuildLog/`)

## Entry structure

Every decision in a daily log file follows this exact pattern:

```
**Decision:** [The decision in one clear sentence]
Context: [Why this decision came up — the situation that forced it]
Reasoning: [Why this option was chosen over alternatives]

---
```

## Formatting rules

- **"Decision:"** is bolded with double asterisks
- **Context:** and **Reasoning:** are NOT bolded (plain text)
- Each entry is separated by a horizontal rule (`---`) on its own line
- Final entry in a file does NOT need a trailing `---`
- No headers, no titles, no preamble at the top of the file — just the entries
- No date repeated inside the file (filename carries the date)

## What counts as a decision worth logging

LOG these:
- Architectural choices (tech stack, framework, structure)
- Naming decisions (what to call something, what conventions to use)
- Scope decisions (what to include, what to defer, what to drop)
- Workflow decisions (how to do something, what tool to use)
- Pivots (changing direction from a previous decision)
- Trade-offs accepted (chose X over Y because…)
- Branding/visibility decisions (public vs. private, internal vs. external)

DO NOT log these:
- Routine work completed (use a build log or session log for that)
- Things you read or watched (use a learning log)
- Open questions still being deliberated (those are not decisions yet)
- Decisions that were considered and rejected (only log the chosen path)

## What goes in Context vs. Reasoning

**Context** = what was happening that made this decision necessary. The triggering situation. What changed, what came up, what was being chosen between.

**Reasoning** = why this specific path was picked. What made this option better than alternatives. The logic behind the choice.

A good test: if someone reads only the Context, they should know what was being decided. If they read only the Reasoning, they should know why this path won.

## Example entry (well-formed)

```
**Decision:** Used .NET 8.0-windows as target framework instead of .NET Standard 2.1
Context: Nice3point Revit API NuGet packages require .NET 8.0 minimum
Reasoning: .NET Standard 2.1 was initially selected but incompatible with the API packages. Manually edited the .csproj file to correct this rather than recreating the project.
```

## Example entry (badly formed — what NOT to do)

```
**Decision:** Made some progress on the project today
Context: Was working on it
Reasoning: Felt like the right thing to do
```

Why bad: not actually a decision, no specific choice made, no rationale, nothing future-you can use.

## Voice and tone

- Past tense ("Used X" not "Will use X")
- Specific not general ("Switched build platform from Any CPU to x64" not "Updated build settings")
- Rationale that holds up later — write Reasoning so it makes sense to future-you who has forgotten the context
- No emotional content, no narrative — these are decision records, not journal entries

## When to log

- Real-time during a session is best (capture before you forget the reasoning)
- End-of-session sweep is acceptable (review what was decided, log the major ones)
- Multi-day reconstruction is unreliable (Reasoning fades fast — do not try to log Tuesday's decisions on Friday)

## Length

- 2–5 entries per day is normal for active work
- 10+ entries per day usually means logging too much routine activity
- 0 entries per day on an active workday usually means decisions were made and not captured — review and add them
