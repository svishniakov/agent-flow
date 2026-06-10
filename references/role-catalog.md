# Role Catalog

This catalog is the lifecycle guard for bundled Agent Flow roles.

Role files are executable prompts in `agents/<role>.md`. This catalog explains why each role exists, when to use it, when not to use it, and where overlap must be resolved by the orchestrator. Keep it in sync with `agents/*.md` and `agents/agent-identities.json`.

Do not create a new role for a one-off lane. Use the existing role plus `lane-map.json` lane metadata. Add a role only when it has durable ownership, repeatable inputs, and distinct verification value.

## Core Orchestration And Planning

### orchestrator

Status: active
Use when: Flow choice, budget selection, dependency gate judgment, trace policy, delegation packets, handoff integration, or final readiness needs a focused orchestration pass.
Do not use when: The main agent can route and verify directly, or when a specialized implementation/review role owns the work.
Overlap notes: Use `architect` for technical design depth, `reviewer` for independent critique, and `qa-verifier` for behavioral evidence.

### product-manager

Status: active
Use when: A user idea needs audience, problem, scope, non-goals, assumptions, and acceptance criteria before architecture or implementation.
Do not use when: Requirements are already specific and testable, or the task is a narrow bugfix.
Overlap notes: Use `documenter` to write final PRD/spec text; use `architect` after product scope is stable.

### architect

Status: active
Use when: Work touches module boundaries, APIs, storage, queues, integrations, migrations, public contracts, or several subsystems, or when code review needs a technical contract for architecture-sensitive readiness.
Do not use when: The plan is already narrow enough for a worker.
Overlap notes: Use `backend-worker`, `frontend-worker`, or language workers for execution after architecture is settled. Use `reviewer` for independent findings against the architect-owned contract.

### researcher

Status: active
Use when: Current facts, docs, APIs, SDK behavior, versions, standards, competitors, or external evidence must be checked.
Do not use when: Local code and docs already prove the needed fact.
Overlap notes: Use `ui-reference-researcher` for visual/UI references; use `rag-retrieval-engineer` for retrieval-quality research.

### documenter

Status: active
Use when: PRDs, specs, README, release notes, lessons, task records, or handoff docs need clear written output.
Do not use when: The task is code execution or independent technical review.
Overlap notes: Use `design-documenter` for `docs/design/DESIGN.md`; use `ai-slops-hunter` for cleanup of already written text.

### reviewer

Status: active
Use when: Implementation, plan, branch, or release readiness needs independent findings ordered by severity.
Do not use when: The need is test execution, browser proof, or reproduction evidence.
Overlap notes: Use `qa-verifier` for behavior checks; reviewer consumes QA evidence but does not replace it. For architecture-sensitive review, reviewer checks the diff against the architect-owned contract instead of replacing architect judgment.

### qa-verifier

Status: active
Use when: Tests, logs, reproduction, browser/simulator smoke, regression checks, and readiness evidence are needed.
Do not use when: The task is architectural critique or code review without execution evidence.
Overlap notes: Use `reviewer` for risk analysis after QA evidence; use workers for fixes.

### ai-slops-hunter

Status: active
Use when: Generated text, code, UI copy, docs, tests, or public artifacts need cleanup for template phrasing, generic naming, empty comments, or machine-like output.
Do not use when: The requested change affects product meaning, API behavior, data contracts, or approved design.
Overlap notes: Use `documenter` for canonical docs creation; use `reviewer` for bug/security/regression findings.

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
Overlap notes: Use `documenter` for PRD/spec/README; use `design-documenter` for design-source truth.

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
