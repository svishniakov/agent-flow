# Bundled Subagents

Agent Flow ships with these subagent definitions in `agents/<role>.md`.
They are used only after the user explicitly asks for subagents and a spawn tool is available.
If no spawn tool is available, use the same role guidance as a solo checklist or role lane only when that still satisfies the user request.

Stable identities live in `agents/agent-identities.json`.

Role frontmatter may list optional skills/plugins. Use them only when available
in the current environment. If a listed skill is missing, follow the role
instructions directly, record the gap when relevant, and continue within the
delegation packet.

## Core orchestration and planning

- `orchestrator` - Главный координатор Codex App: принимает исходную задачу, выбирает режим работы, строит цепочку субагентов, держит критический путь, интегрирует результаты и отвечает пользователю.
- `product-manager` - Продуктовый субагент для превращения идеи в постановку: аудитория, проблема, ценность, ограничения, out-of-scope, PRD-рамка и acceptance criteria.
- `architect` - Архитектурный субагент для implementation plan, границ модулей, Kafka app connectivity, зависимостей, рисков, sequencing, ownership и проверочного критерия.
- `researcher` - Исследовательский субагент для документации, API, SDK, внешних источников, локальных примеров, ограничений и сравнений.
- `documenter` - Документационный субагент для PRD, спецификаций, задач, review-секций, lessons и русской проектной документации.
- `reviewer` - Independent final reviewer for risk, bugs, missing tests, PRD/implementation-plan alignment, quality, security and release readiness.
- `qa-verifier` - QA verification subagent for tests, logs, reproduction, browser/simulator checks, regression risk and readiness assessment.
- `ai-slops-hunter` - AI slop detection and cleanup subagent for text, code, UI/design, docs, copy and generated artifacts.

## Implementation workers

- `backend-worker` - Backend execution subagent for implementing scoped server, API, database, queue, integration or service changes from an approved plan.
- `frontend-worker` - Frontend execution subagent for scoped UI, React, styling, responsive behavior and client-side state changes from an approved plan.
- `typescript-worker` - TypeScript/JavaScript implementation subagent for typed application code, Node/Bun/React modules, API clients, contracts, tests and refactors from an approved plan.
- `bun-worker` - Bun runtime subagent for Bun-based JavaScript/TypeScript projects, scripts, tests, dev servers, package management, lockfiles and Node/npm-to-Bun workflow migration.
- `python-worker` - Python implementation subagent for scoped Python backend, CLI, automation, data processing, QA scripts, tests and dependency hygiene from an approved plan.
- `golang-worker` - Go/Golang implementation subagent for scoped Go services, Kafka producers/consumers, CLIs, packages, concurrency, tests, modules and idiomatic refactors from an approved plan.
- `ios-worker` - iOS execution subagent for scoped SwiftUI, App Intents, simulator, HIG and Apple-platform implementation tasks from an approved plan.
- `rag-retrieval-engineer` - Retrieval-first LLM/RAG engineer for RAG systems, semantic search, chunking, embeddings, reranking, vector databases, graph databases, GraphRAG and retrieval quality evaluation.

## Design flow

- `design-orchestrator` - Дизайн-оркестратор для маршрутизации всех задач по UI, UX, визуальной системе, референсам, Pencil/Figma/Stitch и DESIGN.md.
- `ui-reference-researcher` - Исследователь UI-референсов для лендингов, dashboards, back-office, ботов, мобильных экранов, SaaS, игровых интерфейсов и дизайн-систем.
- `ui-ux-design-director` - Дизайн-директор для выбора UI/UX-концепции под контекст проекта, аудиторию, задачу, референсы, бренд и ограничения реализации.
- `ui-ux-designer` - Neural UI/UX design subagent for turning prompts, PRDs and product constraints into AI-generated screens, UX flows, prototypes, design specs and implementation handoff. Prioritizes Stitch AI generation, Pencil validation and Figma handoff.
- `design-documenter` - Документационный дизайн-субагент для создания и обновления docs/design/DESIGN.md как канонического договора между концепцией, реализацией и проверкой.
- `pencil-designer` - Pencil MCP дизайн-исполнитель для реализации approved DESIGN.md в .pen-файле, работы с переменными, layout, screenshots, exports и визуальной валидацией.
- `design-asset-generator` - Генератор визуальных ассетов для approved DESIGN.md: hero images, product mockups, illustrations, empty states, icons and brand visuals.
- `visual-qa` - Визуальный QA-субагент для проверки Pencil/Figma/site UI against DESIGN.md: layout, overlap, clipped text, responsiveness, accessibility and design intent.

## Growth

- `marketing-growth-strategist` - Marketing and growth strategy subagent for business ideas, business models, GTM, positioning, launch, campaigns, SEO, paid ads, growth loops, sales enablement and promotion strategy.
