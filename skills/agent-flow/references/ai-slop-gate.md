# AI Slop Gate

Use this gate for authorized correction of document and product artifacts: UI/design, generated code, docs, tests, and public content. Ordinary chat replies, explanations, progress updates, review answers and drafts that exist only in chat are outside this gate unless editing is explicitly requested, with the PRD, ADR and Implementation Plan exceptions below. A request for findings only does not authorize cleanup or activate this gate. Document work remains under the mandatory separate orchestrator in [Doc Flow routing](flows.md#маршрутизация-doc-flow); cleanup choices do not authorize root to bypass that router or the document's required author and editor.

## PRD, ADR and Implementation Plan preparation

For a new or substantively revised PRD, follow the mandatory authoring and independent editing sequence in [flows.md](flows.md#подготовка-prd), including when the PRD is delivered only in chat. The separate `ai-slops-hunter` pass is required at every budget; the main author's cleanup cannot replace it. That procedure also defines skill use, preservation of product meaning, return of product questions, rechecks and unavailable participants. Keep the authorized output form; chat delivery does not authorize filesystem writes.

For a new or substantively revised ADR, follow [the ADR procedure](flows.md#подготовка-adr), including chat delivery. A separate `ai-slops-hunter` reads and applies its skill after the author's self-check and `humanizer-ru` for Russian prose. Preserve architectural decisions, contracts, conditions and technical syntax; return disputed choices to the orchestrator. The architect checks preservation before independent review and QA. The editor is mandatory at every budget, and unavailable editing remains unperformed.

For a new or substantively revised Implementation Plan, follow [the plan procedure](implementation-plan-authoring.md), including chat delivery. After preliminary requirements review and the architect's self-check and Russian humanizer pass, a separate `ai-slops-hunter` reads and applies its skill to the whole document. Preserve decisions, stage outcomes, roles, dependencies, conditions, acceptance criteria, numbers and technical syntax. Return product and architectural choices through the orchestrator. The architect checks preservation before independent review and final document QA. The separate editor is mandatory at every budget; unavailable editing stays unperformed.

The optional delegation choices below apply to other artifacts, not to these required document passes.

## Route

1. Read the target artifact and upstream constraints.
2. Apply the cleanup checklist and make minimal corrections in the assigned author by default; outside Doc Flow the main agent may do so. Preserve required separate editors above. Follow the user facing prose rule in `agents/ai-slops-hunter.md`: remove hyphens and dashes from ordinary prose while preserving required syntax in code, commands and other technical fragments.
3. If `standard` or `release` delegation makes the cleanup pass worthwhile and subagent tools are available, read and delegate to the bundled `agents/ai-slops-hunter.md`.
4. If a subagent path was selected but unavailable, say so and continue solo only if that still satisfies the task.
5. Keep edits minimal: remove AI slop without changing scope, behavior, API, data contracts, accepted design, or product meaning.
6. Check user facing prose in the artifact and report for forbidden characters. Verify technical fragments for correctness rather than character absence, then record fixes, checks, blocked work and residual risks.

## Subagent

Canonical bundled agent: `agents/ai-slops-hunter.md`.

User-facing alias: `AI slobhunter` maps to `ai-slops-hunter`.

Edits must be authorized. For PRDs, ADRs and Implementation Plans, use the required separate editor under the procedures above. For other artifacts, delegate when the selected budget permits subagents and the cleanup is worth the coordination cost; otherwise the assigned author performs the same corrections, or the main agent outside Doc Flow. These choices do not bypass mandatory Doc Flow routing or document ownership. Do not delegate a findings only task to this role.

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

For journal-backed work, return one immutable report per assignment under [traceable-runs.md](traceable-runs.md). For a document restricted to chat, return the corrected text and conclusion under [the chat delivery procedure](flows.md#документы-только-в-чате). Include corrections, checks and blocked work with actual evidence. Preserve machine fields, identifiers and native references. Separate execution `status: completed|interrupted` from `outcome: passed|failed|unverified`. Use `passed` only when cleanup and required checks establish the result. A later correction uses a new assignment and report; findings alone do not complete this gate or approve the product.
