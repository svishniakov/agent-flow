# AI Slop Gate

Use this gate for user-facing text, UI/design, generated code, docs, tests, and public artifacts.

## Route

1. Read the target artifact and upstream constraints.
2. If subagent tools are available, read and delegate to `/Users/ucnlejumper/.codex/agents/ai-slops-hunter.md`.
3. If subagents are unavailable, simulate that agent's workflow and record the result in `checks/ai-slop-gate.md`.
4. Keep edits minimal: remove AI slop without changing scope, behavior, API, data contracts, accepted design, or product meaning.
5. Record verdict, checked categories, fixes, checks, and residual risks.

## Subagent

Canonical local agent: `/Users/ucnlejumper/.codex/agents/ai-slops-hunter.md`.

User-facing alias: `AI slobhunter` maps to `ai-slops-hunter`.

Use it before final reviewer on traceable tasks with user-facing output, docs, UI/design, generated code/tests, or public artifacts.

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
