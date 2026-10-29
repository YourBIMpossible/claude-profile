# roadmap-band

Read-only status band above the prompt (Claude Code mods plugin). Shows the model's last trailing `Roadmap:` footer, verified against measured facts (working/idle, session + directory identity, anchor change, tool activity since the line, context and plan-limit usage). Zero model tokens; writes nothing.

- Load for a session: `claude --plugin-dir {profile}\plugins\roadmap-band` (the Roadmap rule itself is already global; see Status)
- Tests: `node test-mock.mjs hooks/register.js` (mock host; proves state logic only, not host rendering)
- Validate: `claude plugin validate .`
- Design, rule text, cost model, pilot plan and review (durable, outside any repo): `{reports}\2026-10-10__roadmap-visibility\` (REPORT.md, PILOT-PLAN.md, REVIEW-register-js.md, drafts/ROADMAP-RULE.draft.md, drafts/rule-block-A.txt)
- Type-check: `tsconfig.json` extends `.claude-plugin/types/tsconfig.json`, which the engine writes (with its own `.gitignore`) when it loads the mod; until the mod has loaded once, there is nothing to extend.
- Status: the Roadmap rule is live for every session through `@ROADMAP-RULE.md` in the global CLAUDE.md. This band stays opt-in (load it with `--plugin-dir`).
