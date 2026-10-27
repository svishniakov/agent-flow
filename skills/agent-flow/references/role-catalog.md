# Role Catalog

This catalog is the lifecycle guard for bundled Agent Flow roles.

Role files are executable prompts in `agents/<role>.md`. This catalog explains why each role exists, when to use it, when not to use it, and where overlap must be resolved by the orchestrator. Keep it in sync with `agents/*.md` and `agents/agent-identities.json`.

Do not create a new role for a one-off lane. Use the existing role with a separate assignment. Add a role only when it has durable ownership, repeatable inputs, and distinct verification value.

## Core Orchestration And Planning

### orchestrator

Status: active
Use when: Flow choice, budget selection, dependency gate judgment, evidence scope, delegation packets, report integration, or final readiness needs a focused orchestration pass.
Do not use when: The main agent can route and verify directly, or when a specialized implementation/review role owns the work.
Overlap notes: Use `architect` for technical design depth, `reviewer` for independent critique, and `qa-verifier` for result evidence. PRD and ADR preparation follow their procedures in [flows.md](flows.md) with required authors, independent editing and document checks. Route disputed ADR decisions immediately to `supervising-architect`.

### product-manager

Status: active
Use when: A PRD needs creation or substantive revision, including from settled requirements, or an idea needs audience, problem, scope, non-goals, assumptions and acceptance criteria.
Do not use when: The task is only implementation or a narrow bugfix with no PRD preparation requested.
Overlap notes: Own the complete PRD under [flows.md](flows.md#подготовка-prd); use the independent editor and document checks defined there. Use `documenter` for other documentation and `architect` for authorized implementation planning after product scope is stable.

### architect

Status: active
Use when: An ADR needs creation or substantive revision, including documentation of an already selected decision, or work touches module boundaries, APIs, storage, queues, integrations, migrations, public contracts, or several subsystems.
Do not use when: The plan is already narrow enough for a worker and no ADR preparation is requested.
Overlap notes: Own the complete ADR under [flows.md](flows.md#подготовка-adr); use the required ADR skill and guard, send disputes to `supervising-architect`, and preserve the independent editor, reviewer and QA. Use workers for execution after architecture is settled.

### supervising-architect

Status: active
Use when: An ADR decision is disputed, including a substantive guard finding, or a reproduced failure remains after ordinary architecture review and needs independent diagnosis.
Do not use when: An ordinary correction needs no architectural choice, or the only issue is an unavailable check.
Overlap notes: Resolve ADR disputes immediately under [flows.md](flows.md#подготовка-adr), apply the guard and record the chosen decision in the document. This makes the supervisor a coauthor, not the final independent reviewer. Keep `architect` responsible for the complete ADR; no failed retry count is required.

### researcher

Status: active
Use when: Current facts, docs, APIs, SDK behavior, versions, standards, competitors, or external evidence must be checked.
Do not use when: Local code and docs already prove the needed fact.
Overlap notes: Use `ui-reference-researcher` for visual/UI references; use `rag-retrieval-engineer` for retrieval-quality research.

### documenter

Status: active
Use when: Technical specs, README, release notes, lessons, task records, or handoff docs need clear written output.
Do not use when: A PRD or ADR needs creation or substantive revision, or the task is code execution or independent technical review.
Overlap notes: Use `product-manager` for complete PRD authorship, `architect` for complete ADR authorship, `design-documenter` for `docs/design/DESIGN.md`, and `ai-slops-hunter` for cleanup of already written text.

### reviewer

Status: active
Use when: PRD or ADR content, implementation, plan, branch, or release readiness needs independent findings ordered by severity.
Do not use when: The need is test execution, browser proof, or reproduction evidence.
Overlap notes: PRD and ADR review check the document under [flows.md](flows.md), followed by document QA. Reviewer applies `arch-simplicity-guard` to architecture and plans before dependent implementation and sends disputed ADR choices through the orchestrator to the supervisor. Remain independent of all authors, including the supervisor and editor. Plan review does not replace code review before product QA.

### qa-verifier

Status: active
Use when: Requirements and acceptance criteria need pre-development review (`qa.requirements`), PRD or ADR readiness needs verification, or tests, logs, reproduction, browser/simulator smoke, regression checks and readiness evidence are needed.
Do not use when: The task is architectural critique or code review without execution evidence.
Overlap notes: Requirements review identifies gaps for product-manager or architect without authoring their decisions. PRD and ADR document QA follow successful independent review under [flows.md](flows.md) and do not require application execution. Neither replaces final implementation QA. Remain independent of all authors, including the supervisor and editor, and of the reviewer.

### senior-qa-verifier

Status: active
Use when: A reproduced failure exposes a concrete uncertainty in criteria, QA adequacy or test design that ordinary verification has not resolved.
Do not use when: Ordinary QA is sufficient, or the task needs implementation or an architecture decision.
Overlap notes: Use `qa-verifier` for routine checks. Senior QA identifies the smallest missing scenario and sends any product or technical decision to its owner; no fixed review chain or full edge-case matrix is required.

### ai-slops-hunter

Status: active
Use when: Generated text, code, UI copy, docs, tests, or public artifacts need authorized correction of template phrasing, generic naming, empty comments, or inflated output. The ban on hyphens and dashes applies only to user facing prose; preserve required technical syntax.
Do not use when: The task allows findings only, forbids edits, or the requested change affects protected product meaning, API behavior, data contracts, or approved design.
Overlap notes: Use `product-manager` for PRD authorship, `architect` for ADR authorship and `documenter` for other canonical docs. This role performs the required separate PRD and ADR editing under [flows.md](flows.md), preserving decisions. Architectural disputes return through the orchestrator; they are not resolved by stylistic edits. Use `reviewer` for independent findings.

## Implementation Workers

### backend-worker

Status: active
Use when: Server code, APIs, DB/storage, queues, auth, integrations, or background jobs need scoped implementation.
Do not use when: The work is primarily UI, TypeScript-only shared code, or architecture planning.
Overlap notes: Use `typescript-worker` for shared TS modules; use `architect` before broad contract changes.

### frontend-worker

Status: active
Use when: UI, React, styling, responsive behavior, forms, navigation, and client state need implementation from an approved scope/design.
Do not use when: The work is generic TS/JS backend/shared modules or visual concept selection.
Overlap notes: Use `typescript-worker` for non-visual TS/JS; use design roles before non-trivial UI implementation.

### typescript-worker

Status: active
Use when: TypeScript/JavaScript modules, shared types, Node/Bun/React code, API clients, tests, or scoped refactors need implementation.
Do not use when: The work is primarily visual UI polish, Bun package workflow, or backend domain design.
Overlap notes: Use `frontend-worker` for UI behavior/styling; use `bun-worker` for Bun runtime/package workflow.

### bun-worker

Status: active
Use when: Bun scripts, `bun.lock`, Bun tests, dev servers, dependency workflow, or package-manager migration is the main work.
Do not use when: The task is ordinary TS/JS implementation with no Bun-specific workflow.
Overlap notes: Use `typescript-worker` for code changes; use `bun-worker` for runtime/tooling behavior.

### python-worker

Status: active
Use when: Python backend, CLI, automation, data processing, QA scripts, PDF/RAG utilities, tests, or dependency hygiene need scoped work.
Do not use when: The task is only docs, research, or non-Python implementation.
Overlap notes: Use `rag-retrieval-engineer` for retrieval strategy; use `qa-verifier` for independent verification.

### golang-worker

Status: active
Use when: Go services, CLIs, packages, concurrency, Kafka producers/consumers, tests, modules, or idiomatic refactors need scoped implementation.
Do not use when: The work is architecture-only or not Go-specific.
Overlap notes: Use `architect` for cross-system design; use `qa-verifier` for independent test/smoke evidence.

### ios-worker

Status: active
Use when: SwiftUI, App Intents, Apple-platform state/navigation, simulator-tested workflows, or HIG-sensitive implementation needs scoped work.
Do not use when: The task is generic product/design planning with no Apple implementation.
Overlap notes: Use `ui-ux-design-director` for concept direction; use iOS worker for execution.

### rag-retrieval-engineer

Status: active
Use when: Retrieval quality, ingestion, chunking, embeddings, vector stores, hybrid search, reranking, GraphRAG, ACL, citations, or RAG evaluation is central.
Do not use when: The task is generic LLM prompting or answer generation without retrieval-quality work.
Overlap notes: Use `researcher` for external facts; use `python-worker` or `backend-worker` for implementation after retrieval design is clear.

## Design Flow

### design-orchestrator

Status: active
Use when: UI/UX work needs routing across design source, references, concept, `DESIGN.md`, Pencil/Figma/Stitch, implementation, and visual QA.
Do not use when: The task is a tiny UI bugfix or copy tweak with no design-route uncertainty.
Overlap notes: Use this role before specialized design roles when maturity or ownership is unclear.

### ui-reference-researcher

Status: active
Use when: Real UI examples, screenshots, design-system references, or pattern lessons are needed before choosing a direction.
Do not use when: The final design direction is already approved.
Overlap notes: Use `researcher` for non-visual facts; use `ui-ux-design-director` to choose the concept.

### ui-ux-design-director

Status: active
Use when: Product context, references, brand, audience, and implementation limits must become one chosen UI/UX direction.
Do not use when: The task is pixel execution, asset generation, or QA against an approved design.
Overlap notes: Use `ui-ux-designer` after direction selection; use `visual-qa` after implementation/prototype.

### ui-ux-designer

Status: active
Use when: Approved direction needs screen flows, prototypes, visual specs, component guidance, or implementation handoff.
Do not use when: No product/design direction exists, or the work is frontend implementation.
Overlap notes: Use `ui-ux-design-director` first for direction; use `frontend-worker` for code.

### design-documenter

Status: active
Use when: `docs/design/DESIGN.md` must capture design status, flows, visual system, components, states, assets, checks, and risks.
Do not use when: The task is general documentation unrelated to design contracts.
Overlap notes: Use `product-manager` for PRDs, `documenter` for technical specs and README, and `design-documenter` for design-source truth.

### pencil-designer

Status: active
Use when: Approved `DESIGN.md` work must be implemented in a `.pen` artifact with variables, layout, screenshots, exports, and validation.
Do not use when: The design direction is not approved or the task is Figma-only.
Overlap notes: Use `ui-ux-designer` for concept/spec; use `visual-qa` to check the resulting artifact.

### design-asset-generator

Status: active
Use when: Approved design direction needs hero images, product mockups, illustrations, empty states, icons, or brand visuals.
Do not use when: The asset request is exploratory without design constraints, or when licensing/source constraints are unclear.
Overlap notes: Use `ui-ux-design-director` for direction; use `visual-qa` for final fit.

### visual-qa

Status: active
Use when: Pencil/Figma/site UI must be checked against `DESIGN.md` for layout, overlap, clipped text, responsiveness, contrast, focus, states, assets, and design intent.
Do not use when: The task is redesign or implementation.
Overlap notes: Use `frontend-worker`, `pencil-designer`, or `design-asset-generator` to fix issues after QA.

## Growth

### marketing-growth-strategist

Status: active
Use when: GTM, positioning, launch, campaigns, SEO, paid ads, growth loops, sales enablement, ICP, funnel, or lifecycle strategy is in scope.
Do not use when: The task needs only product definition, implementation, or current-fact research.
Overlap notes: Use `researcher` for competitor/current market evidence; use `product-manager` for product scope.
