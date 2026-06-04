# AI Slop Gate

Use this gate for user-facing text, UI/design, generated code, docs, tests, and public artifacts.

## Route

1. Read the target artifact and upstream constraints.
2. Simulate the checklist in the main agent by default.
3. If the user explicitly requested subagents and subagent tools are available, read and delegate to the bundled `agents/ai-slops-hunter.md`.
4. If subagents were requested but unavailable, say so and continue solo only if that still satisfies the request.
5. Keep edits minimal: remove AI slop without changing scope, behavior, API, data contracts, accepted design, or product meaning.
6. Record verdict, checked categories, fixes, checks, and residual risks.

## Subagent

Canonical bundled agent: `agents/ai-slops-hunter.md`.

User-facing alias: `AI slobhunter` maps to `ai-slops-hunter`.

Use it only when the user explicitly requested subagents. Otherwise, run the same checklist in the main agent.

Required packet fields:

- target artifact type: text, code, UI/design, docs, generated asset, or mixed;
- exact scope and protected meaning or behavior;
- run directory and `handoffs/ai-slops-hunter.md` path when traceable;
- files, screenshots, design docs, PRD, or artifacts to read first;
- allowed changes and forbidden changes;
- expected output: findings only, patch, or patch plus findings;
- checks to run after edits;
- Definition of Done gates;
- `Speed: Standard; do not use Fast`.

## Related Skills

Use the narrowest applicable skill from the subagent description:

- `humanizer-ru`: Russian docs and product text.
- `humanize-ts`: TypeScript/JavaScript cleanup.
- `english-humanizer`: routine English prose.
- `humanize-text`: English long-form only when privacy, dependency, and semantic-drift risks are acceptable.
- `copy-editing` and `grammar-check`: text quality.
- `code-review-excellence`: generated code and tests.
- `frontend-design` and `accessibility`: UI/design slop that affects usability.
- `agent-governance` and `ai-agents-architect`: role boundaries or workflow rules.

## Verdicts

- `pass`: no material slop found.
- `pass-with-risks`: minor issues remain or checks are limited.
- `fail`: slop remains inside assigned scope.
- `blocked`: safe cleanup would change scope, behavior, API, data contract, or approved design.
