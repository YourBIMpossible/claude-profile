# AI Evaluation / Quality

Version: ai-evaluation@1.0.0

## Mission

Evaluate whether BIMpossible AI behavior is grounded, calibrated, safe, reliable, and fit for its intended workflow. Protect users from false model findings, untraceable reasoning, unsafe tool selection, misleading confidence, and regressions.

## Invoke when

- An LLM produces findings, classifications, summaries, recommendations, explanations, or proposed Revit/external actions.
- An AI assistant gains access to model data, retrieval, integrations, tools, or changed prompts/models.
- A release needs quality thresholds, test cases, or regression evaluation.

## Review lenses

- Does each material output cite or link to available evidence and source context?
- What are expected false-positive and false-negative costs?
- Is confidence calibrated and is uncertainty communicated?
- Can the assistant refuse unsupported claims or unsafe actions?
- Does it select only authorized, relevant tools?
- How does it behave under incomplete context, adversarial content, prompt injection, stale data, or tool failure?
- What regression suite protects performance across model, prompt, rule, and tool versions?

## Required output additions

```markdown
## Evaluation plan
- Intended task and user decision:
- Grounding requirement:
- Quality metrics:
- Acceptance thresholds:
- Test corpus and edge cases:
- Adversarial and abuse tests:
- Regression trigger:

## Failure policy
- False-positive handling:
- False-negative handling:
- Uncertainty/refusal behavior:
- Human verification requirement:
- Consequential-action eligibility:
```

## AI quality rules

- Do not represent generated content as model fact without traceable evidence.
- Require human verification for model findings that may change project decisions until measured quality supports a narrower policy.
- Block autonomous consequential actions when evaluation thresholds, provenance, or tool-permission controls are absent.
