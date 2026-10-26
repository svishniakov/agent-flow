# AI Slop Gate

Use this gate for authorized correction of document and product artifacts: UI/design, generated code, docs, tests, and public content. Ordinary chat replies, explanations, progress updates, review answers and drafts that exist only in chat are outside this gate, regardless of length or subject. Do not invoke `humanizer-ru` or `ai-slops-hunter` for them unless the user explicitly requests editing. A request for findings only does not authorize cleanup or activate this gate.

## Route

1. Read the target artifact and upstream constraints.
2. Apply the cleanup checklist and make minimal corrections in the main agent by default. Follow the mandatory character ban in `agents/ai-slops-hunter.md`, including its rules for blocked fragments.
3. If `standard` or `release` delegation makes the cleanup pass worthwhile and subagent tools are available, read and delegate to the bundled `agents/ai-slops-hunter.md`.
4. If a subagent path was selected but unavailable, say so and continue solo only if that still satisfies the task.
5. Keep edits minimal: remove AI slop without changing scope, behavior, API, data contracts, accepted design, or product meaning.
6. Check the corrected artifact and report prose for forbidden characters, then record fixes, checks, blocked work and residual risks.

## Subagent

Canonical bundled agent: `agents/ai-slops-hunter.md`.

User-facing alias: `AI slobhunter` maps to `ai-slops-hunter`.

Use it only when edits are authorized, the selected budget permits subagents and the cleanup is worth the coordination cost. Otherwise, perform the same corrections in the main agent when edits are authorized. Do not delegate a findings only task to this role.

Required packet fields:

- target artifact type: text, code, UI/design, docs, generated asset, or mixed;
- exact scope and protected meaning or behavior;
- assignment ID and current report references;
- files, screenshots, design docs, PRD, or artifacts to read first;
- allowed changes and forbidden changes;
- expected output: corrected artifact or minimal patch, plus the shared report;
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

Return one current report under [traceable-runs.md](traceable-runs.md). Put corrections, checks and blocked work in the summary, with actual evidence references. Preserve required machine fields, identifiers and native references. Use `completed` when authorized cleanup is finished and required checks establish the result; use `failed` when a mandatory correction or check remains blocked. Findings alone do not complete this gate, and a report status alone does not approve the product.
