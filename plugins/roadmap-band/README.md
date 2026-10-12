# roadmap-band

Read-only status band above the prompt (Claude Code mods plugin). Shows the model's last trailing `Roadmap:` footer, verified against measured facts (working/idle, session + directory identity, anchor change, tool activity since the line, context and plan-limit usage). Zero model tokens; writes nothing.

- Load for a session: `claude --plugin-dir F:\Claude-Profile\plugins\roadmap-band --append-system-prompt "<rule block A>"`
- Tests: `node test-mock.mjs hooks/register.js` (mock host; proves state logic only, not host rendering)
- Validate: `claude plugin validate .`
- Design, rule text, cost model, pilot plan and review (durable, outside any repo): `F:\Claude-Tools\reports\2026-10-10__roadmap-visibility\` (REPORT.md, PILOT-PLAN.md, REVIEW-register-js.md, drafts/ROADMAP-RULE.draft.md, drafts/rule-block-A.txt)
- Status: pilot only. The global CLAUDE.md rule stays unchanged until the pilot checklist passes.
