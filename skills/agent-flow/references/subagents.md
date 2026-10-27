# Существующие роли

Роли находятся в `agents/<role>.md`, идентичности — в `agents/agent-identities.json`. Выбирайте существующую роль по ответственности; отдельное назначение той же роли не требует нового role file.

Порядок запуска, модели и пакет задания: [delegation.md](delegation.md). Один завершённый native-ответ становится одним принятым отчётом по [traceable-runs.md](traceable-runs.md).

Для положительного завершения изменения файлов нужны независимые реальные `qa-verifier` и `reviewer` при любом масштабе. Остальные агенты добавляются по пользе для задачи. Недоступная обязательная роль не заменяется переименованием root или другого агента.

Подготовка PRD требует автора `product-manager` и отдельного редактора `ai-slops-hunter` по [flows.md](flows.md#подготовка-prd), включая документ только в чате. Эти назначения не становятся необязательными при уменьшении масштаба.

Подготовка ADR требует автора `architect` и отдельного редактора `ai-slops-hunter` по [flows.md](flows.md#подготовка-adr), включая документ только в чате. Спорные решения немедленно передаются `supervising-architect` для выбора и внесения в документ. Старший архитектор становится соавтором; итоговые reviewer и QA остаются независимыми от всех участников авторства. `senior-qa-verifier` не является дополнительной обязательной ступенью ADR.

Skills в frontmatter выбираются по предмету поручения. Их список не разрешает установку и не требует запускать все инструменты.

## Core orchestration and planning

- `orchestrator` - Agent Flow orchestration support subagent for routing, dependency gate checks, sequencing, report integrity, delegation packets, verification evidence, and final integration under the explicit Agent Flow invocation model.
- `product-manager` - Product subagent owning PRD creation and substantive revision, coherent product scope, user outcomes, constraints, and acceptance criteria.
- `architect` - Architecture subagent owning ADR creation and revision, technical decisions, implementation plans, boundaries, contracts, risks, and verification criteria.
- `supervising-architect` - Senior architecture subagent resolving disputed ADR decisions, recording the chosen approach, and reassessing unresolved architecture problems.
- `researcher` - Research subagent for documentation, APIs, SDKs, external sources, local examples, constraints, comparisons, and source-backed findings.
- `documenter` - Documentation subagent for technical specifications, task records, review sections, lessons, release notes, README work, and project documentation outside PRD and ADR authorship.
- `reviewer` - Independent reviewer for PRD and ADR content, architecture and plans before implementation, and code before QA for correctness, evidence, simplicity, security, and readiness.
- `qa-verifier` - QA verification subagent for requirements, PRD and ADR readiness, tests, logs, reproduction, browser or simulator checks, and evidence of completed work.
- `senior-qa-verifier` - Senior QA subagent for blocked resolution recovery, acceptance criteria review, test design, edge cases, negative cases, and QA adequacy checks.
- `ai-slops-hunter` - AI slop cleanup subagent for text, code, UI, docs, tests, and generated artifacts. Bans hyphens and dashes only in user facing prose and preserves technical syntax.

## Implementation workers

- `backend-worker` - Backend execution subagent for scoped server, API, database, queue, integration, auth, or service changes from an approved plan.
- `frontend-worker` - Frontend execution subagent for scoped UI, React, styling, responsive behavior, and client-state changes from an approved plan.
- `typescript-worker` - TypeScript/JavaScript implementation subagent for typed app code, Node/Bun/React modules, API clients, contracts, tests, and scoped refactors from an approved plan.
- `bun-worker` - Bun runtime subagent for Bun-based JavaScript/TypeScript projects, scripts, tests, dev servers, package management, lockfiles, and Node/npm-to-Bun migration.
- `python-worker` - Python implementation subagent for scoped backend, CLI, automation, data processing, QA scripts, tests, PDF/RAG utilities, and dependency hygiene from an approved plan.
- `golang-worker` - Go implementation subagent for scoped Go services, Kafka producers/consumers, CLIs, packages, concurrency, tests, modules, and idiomatic refactors from an approved plan.
- `ios-worker` - iOS execution subagent for scoped SwiftUI, App Intents, simulator, HIG, and Apple-platform implementation tasks from an approved plan.
- `rag-retrieval-engineer` - Retrieval-first LLM/RAG engineer for semantic search, chunking, embeddings, reranking, vector stores, graph databases, GraphRAG, and retrieval quality evaluation.

## Design flow

- `design-orchestrator` - Design orchestration subagent for UI, UX, visual systems, references, Pencil, Figma, Stitch, and DESIGN.md routing.
- `ui-reference-researcher` - UI reference research subagent for landing pages, dashboards, back-office tools, bots, mobile screens, SaaS, game UI, and design systems.
- `ui-ux-design-director` - Design-director subagent for choosing a UI/UX concept from project context, audience, references, brand, and implementation constraints.
- `ui-ux-designer` - Neural UI/UX design subagent for turning prompts, PRDs, and product constraints into generated screens, flows, prototypes, design specs, and implementation report.
- `design-documenter` - Design documentation subagent for creating and updating docs/design/DESIGN.md as the contract between concept, implementation, and verification.
- `pencil-designer` - Pencil MCP design implementation subagent for approved DESIGN.md work in .pen files, variables, layout, screenshots, exports, and visual validation.
- `design-asset-generator` - Visual asset generation subagent for approved DESIGN.md work: hero images, product mockups, illustrations, empty states, icons, and brand visuals.
- `visual-qa` - Visual QA subagent for checking Pencil, Figma, screenshots, or site UI against DESIGN.md: layout, overlap, clipped text, responsiveness, accessibility, and design intent.

## Growth

- `marketing-growth-strategist` - Marketing and growth strategy subagent for business ideas, GTM, positioning, launch, campaigns, SEO, paid ads, growth loops, sales enablement, and promotion strategy.
