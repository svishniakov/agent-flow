# Role Catalog

This catalog is the lifecycle guard for bundled Agent Flow roles.

Role files are executable prompts in `agents/<role>.md`. This catalog explains why each role exists, when to use it, when not to use it, and where overlap must be resolved by the orchestrator. Keep it in sync with `agents/*.md` and `agents/agent-identities.json`.

Do not create a new role for a one-off lane. Use the existing role with a separate assignment. Add a role only when it has durable ownership, repeatable inputs, and distinct verification value.

## Core Orchestration And Planning

### orchestrator

Status: active
Use when: Any document task within explicitly invoked Agent Flow requires this separate router, including document work in other flows; outside Doc Flow, use when routing, dependencies or final readiness need focused orchestration.
Do not use when: The task is only discussion of the skill, or a non-document task can be routed by the main agent without a separate pass.
Overlap notes: Own Doc Flow routing under [flows.md](flows.md#маршрутизация-doc-flow), with the actual root doing technical direct-child launches and registration from your packets. Do not replace root identity or create a parallel journal. Use product-manager, architect or documenter for content and independent reviewer/QA for acceptance. PRD, ADR and Implementation Plan preserve their required authors, editing and checks; disputed architectural choices go immediately to the supervisor. Design-orchestrator provides subject expertise without replacing this routing ownership.

### product-manager

Status: active
Use when: A PRD needs creation or substantive revision, including from settled requirements, or an idea needs audience, problem, scope, non-goals, assumptions and acceptance criteria.
Do not use when: The task is only implementation or a narrow bugfix with no PRD preparation requested.
Overlap notes: Own the complete PRD under [flows.md](flows.md#подготовка-prd); use the independent editor and document checks defined there. Use `documenter` for other documentation and `architect` for authorized implementation planning after product scope is stable.

### architect

Status: active
Use when: An ADR or Implementation Plan needs creation or substantive revision, including documentation of already selected decisions, or work touches module boundaries, APIs, storage, queues, integrations, migrations, public contracts, or several subsystems.
Do not use when: A task is narrow enough for a worker and no ADR or Implementation Plan preparation is requested.
Overlap notes: Own the complete ADR under [flows.md](flows.md#подготовка-adr) and complete plan under [its procedure](implementation-plan-authoring.md), routed by the mandatory orchestrator. Apply relevant skills and guard, send architectural disputes to the supervisor and preserve required editing, review and document QA. Documenter does not take over final text. Use workers for execution after decisions are settled and approved.

### supervising-architect

Status: active
Use when: An ADR or Implementation Plan has a disputed architectural choice, including a guard finding requiring such a choice, or a reproduced failure remains after ordinary architecture review and needs independent diagnosis.
Do not use when: An ordinary correction needs no architectural choice, or the only issue is an unavailable check.
Overlap notes: Resolve disputes immediately under the [ADR](flows.md#подготовка-adr) or [plan procedure](implementation-plan-authoring.md), apply guard and directly record the decision in the assigned document. This makes the supervisor a coauthor, not final independent reviewer or QA. Keep architect responsible for the whole document; no failed retry count is required. Do not hide a revision of accepted ADR decisions in a plan.

### researcher

Status: active
Use when: Current facts, docs, APIs, SDK behavior, versions, standards, competitors, or external evidence must be checked.
Do not use when: Local code and docs already prove the needed fact.
Overlap notes: Use `ui-reference-researcher` for visual/UI references; use `rag-retrieval-engineer` for retrieval-quality research.

### documenter

Status: active
Use when: Technical specs, README, release notes, lessons, task records, or handoff docs need clear written output.
Do not use when: A PRD, ADR or Implementation Plan needs creation or substantive revision, or the task is code execution or independent technical review.
Overlap notes: Under the mandatory Doc Flow orchestrator, use product-manager for full PRD authorship, architect for full ADR and Implementation Plan authorship, design-documenter for docs/design/DESIGN.md and ai-slops-hunter for required editing of existing text.

### reviewer

Status: active
Use when: PRD, ADR or Implementation Plan content, implementation, branch, or release readiness needs independent findings ordered by severity.
Do not use when: The need is test execution, browser proof, or reproduction evidence.
Overlap notes: Review documents under [flows.md](flows.md) and [the plan procedure](implementation-plan-authoring.md), then preserve independent document QA. Apply guard to architecture and plans and return architectural disputes through the mandatory orchestrator to the supervisor. Use only local substantive criteria from feature-review-plan, without PR commands or conditional verdicts. Remain independent of all authors, including supervisor and editor; document review does not replace future code review and product QA.

### qa-verifier

Status: active
Use when: Requirements and acceptance criteria need pre-development review (`qa.requirements`), PRD, ADR or Implementation Plan readiness needs verification, or tests, logs, reproduction, browser/simulator smoke, regression checks and readiness evidence are needed.
Do not use when: The task is architectural critique or code review without execution evidence.
Overlap notes: Preliminary requirements review requires qa-requirement-reviewer and identifies gaps without authoring decisions; it does not replace final document QA. PRD, ADR and Implementation Plan document QA follow successful independent review under their procedures and do not require future application implementation. Return findings through the mandatory orchestrator, using PASS/FAIL without conditional clearance. Remain independent of all authors, supervisor, editor and reviewer; document checks do not replace later product QA.

### senior-qa-verifier

Status: active
Use when: A reproduced failure exposes a concrete uncertainty in criteria, QA adequacy or test design that ordinary verification has not resolved.
Do not use when: Ordinary QA is sufficient, or the task needs implementation or an architecture decision.
Overlap notes: Use `qa-verifier` for routine checks. Senior QA identifies the smallest missing scenario and sends any product or technical decision to its owner; no fixed review chain or full edge-case matrix is required.

### ai-slops-hunter

Status: active
Use when: Generated text, code, UI copy, docs, tests, or public artifacts need authorized correction of template phrasing, generic naming, empty comments, or inflated output. The ban on hyphens and dashes applies only to user facing prose; preserve required technical syntax.
Do not use when: The task allows findings only, forbids edits, or the requested change affects protected product meaning, API behavior, data contracts, or approved design.
Overlap notes: Use product-manager for PRD authorship, architect for ADR and Implementation Plan authorship and documenter for other canonical docs. Perform required separate editing for PRD, ADR and Implementation Plan under their procedures, preserving decisions, dependencies and criteria. Return architectural choices through the mandatory orchestrator; stylistic edits do not resolve them. Use reviewer for independent findings.

## Implementation Workers

### backend-worker

Status: active
Use when: Server code, APIs, DB/storage, queues, auth, integrations, or background jobs need scoped implementation.
Do not use when: The work is primarily UI, TypeScript-only shared code, or architecture planning.
Overlap notes: Use `elixir-phoenix-worker` for Elixir/Phoenix implementation and `typescript-worker` for shared TS modules; use `architect` before broad contract changes.

### frontend-worker

Status: active
Use when: UI, React, styling, responsive behavior, forms, navigation, and client state need implementation from an approved scope/design.
Do not use when: The work is generic TS/JS backend/shared modules or visual concept selection.
Overlap notes: Use `astro-worker` for Astro pages and components, `elixir-phoenix-worker` for Phoenix HEEx/LiveView, and `typescript-worker` for non-visual TS/JS. Use design roles before non-trivial UI implementation.

### astro-worker

Status: active
Use when: Astro HTML pages, layouts, components, styles, routes, images, Markdown/MDX, content collections, or selective hydration need scoped implementation from approved requirements and applicable design.
Do not use when: Design or architecture must be selected first, only independent visual verification is needed, or the main implementation does not use Astro.
Overlap notes: Own Astro page implementation even when Bun runs the project. Use `bun-worker` only for a separate Bun runtime or package workflow assignment and `typescript-worker` for shared code outside the Astro task. Keep one owner per assigned path; use design roles for concept decisions and `visual-qa` for independent visual evidence.

### typescript-worker

Status: active
Use when: TypeScript/JavaScript modules, shared types, Node/Bun/React code, API clients, tests, or scoped refactors need implementation.
Do not use when: The work is primarily visual UI polish, Bun package workflow, or backend domain design.
Overlap notes: Use `astro-worker` for Astro pages and components, `frontend-worker` for other UI behavior/styling, and `bun-worker` for Bun runtime/package workflow.

### bun-worker

Status: active
Use when: Bun scripts, `bun.lock`, Bun tests, dev servers, dependency workflow, or package-manager migration is the main work.
Do not use when: The task is ordinary TS/JS implementation or Astro page work with no Bun-specific workflow.
Overlap notes: Use `astro-worker` for Astro pages and `typescript-worker` for other code changes. Using Bun to run an Astro project does not by itself require a separate Bun executor.

### python-worker

Status: active
Use when: Python backend, CLI, automation, data processing, QA scripts, PDF/RAG utilities, tests, or dependency hygiene need scoped work.
Do not use when: The task is only docs, research, or non-Python implementation.
Overlap notes: Use `rag-retrieval-engineer` for retrieval strategy; use `qa-verifier` for independent verification.

### elixir-phoenix-worker

Status: active
Use when: Elixir domain logic, Phoenix APIs, HEEx/LiveView, Channels, Ecto persistence and migrations, OTP processes, Mix tooling, or tests need scoped implementation. Elixir without Phoenix is also in scope.
Do not use when: Architecture must be selected first, the main stack is not Elixir, or only independent behavior verification is needed.
Overlap notes: Own Elixir/Phoenix implementation instead of a generic backend/frontend worker, including LiveView behavior and HEEx. Use `architect` for material context, persistence, supervision, or public contract decisions and `qa-verifier` for independent evidence. Design roles own visual direction.

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
Overlap notes: Use this role for design-stage expertise when maturity or ownership is unclear. For document work the mandatory orchestrator under Doc Flow owns assignments, sequencing and rechecks; this role returns recommendations to it rather than creating a parallel document route.

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
Overlap notes: Use `ui-ux-design-director` first for direction. Route code to `astro-worker` for Astro, `elixir-phoenix-worker` for HEEx/LiveView, or `frontend-worker` for other web UI.

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
Overlap notes: Return code issues to the assigned stack owner: `astro-worker`, `elixir-phoenix-worker`, or `frontend-worker`. Use `pencil-designer` or `design-asset-generator` for artifact or asset fixes.

## Growth

### marketing-growth-strategist

Status: active
Use when: GTM, positioning, launch, campaigns, SEO, paid ads, growth loops, sales enablement, ICP, funnel, or lifecycle strategy is in scope.
Do not use when: The task needs only product definition, implementation, or current-fact research.
Overlap notes: Use `researcher` for competitor/current market evidence; use `product-manager` for product scope.
