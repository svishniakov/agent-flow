# AI Slop Gate

Use this gate for document and product artifacts: UI/design, generated code, docs, tests, and public content. Ordinary chat replies, explanations, progress updates, review answers and drafts that exist only in chat are outside this gate, regardless of length or subject. Do not invoke `humanizer-ru` or `ai-slops-hunter` for them unless the user explicitly requests that check. Document review is still covered; its chat delivery does not require another pass.

## Route

1. Read the target artifact and upstream constraints.
2. Simulate the checklist in the main agent by default.
3. If `standard` or `release` delegation makes the added check worthwhile and subagent tools are available, read and delegate to the bundled `agents/ai-slops-hunter.md`.
4. If a subagent path was selected but unavailable, say so and continue solo only if that still satisfies the task.
5. Keep edits minimal: remove AI slop without changing scope, behavior, API, data contracts, accepted design, or product meaning.
6. Record verdict, checked categories, fixes, checks, and residual risks.

## Subagent

Canonical bundled agent: `agents/ai-slops-hunter.md`.

User-facing alias: `AI slobhunter` maps to `ai-slops-hunter`.

Use it only when the selected budget permits subagents and the added check is worth the coordination cost. Otherwise, run the same checklist in the main agent.

Required packet fields:

- target artifact type: text, code, UI/design, docs, generated asset, or mixed;
- exact scope and protected meaning or behavior;
- assignment ID and current report references;
- files, screenshots, design docs, PRD, or artifacts to read first;
- allowed changes and forbidden changes;
- expected output: findings only, patch, or patch plus findings;
- checks to run after edits;
- applicable acceptance criteria;

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

## Report

Return one current report under [traceable-runs.md](traceable-runs.md). Put findings, corrections and limitations in the summary, with actual evidence references. Use `completed` when the assigned review or cleanup is finished and `failed` when it cannot establish the required result. Findings remain concrete; a report status alone does not approve the product.
