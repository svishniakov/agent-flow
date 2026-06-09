# Bundled Subagents

Agent Flow ships with these subagent definitions in `agents/<role>.md`.
They are used only after the user explicitly asks for subagents and a spawn tool is available.
If no spawn tool is available, use the same role guidance as a solo checklist or role lane only when that still satisfies the user request.

Stable identities live in `agents/agent-identities.json`.

Role lifecycle guidance lives in `references/role-catalog.md`: use cases, exclusions, overlap notes, and the rule against one-off roles.

Role frontmatter `skills:` is a dependency demand, not an install command. Use listed skills only when they are available in the current environment. If a listed skill is missing, follow the role instructions directly, record the gap when relevant, and continue within the delegation packet.

Canonical install metadata lives in `registries/agent-skills.json`: tier, role usage, aliases, target paths, prompts, plugin/manual notes, and allowlisted commands. Run `python3 scripts/check-agent-deps.py --post-install` after clone, or `--scope core --install-plan` / `--scope full --install-plan` for a non-interactive audit. The checker never performs silent installs. `--guided-install` requires explicit confirmation and executes only allowlisted `git`/`local` commands; `plugin`, `prompt`, and `manual` entries are printed only.

Role frontmatter also defines runtime model settings:

- `model` and `reasoning_effort`: default values passed to `spawn_agent`.
- `service_tier`: optional default service tier passed only when present.
- `escalation_model`, `escalation_reasoning_effort`, and `escalation_service_tier`: optional overrides used only after a matching trigger.
- `escalation_triggers`: inline list of task/risk triggers that activate escalation through `resolve-agent-config.py --trigger <trigger>`.

Run `python3 scripts/validate-agent-config.py` and `python3 scripts/validate-role-catalog.py` after editing role files.

## Core orchestration and planning

- `orchestrator` - Agent Flow orchestration support subagent for routing, sequencing, trace hygiene, delegation packets, verification evidence, and final integration under the explicit Agent Flow invocation model.
- `product-manager` - Product subagent for turning an idea into scope, audience, problem, value, constraints, non-goals, PRD frame, and acceptance criteria.
- `architect` - Architecture subagent for implementation plans, module boundaries, dependencies, sequencing, ownership, risk, and verification criteria.
- `researcher` - Research subagent for documentation, APIs, SDKs, external sources, local examples, constraints, comparisons, and source-backed findings.
- `documenter` - Documentation subagent for PRDs, specifications, task records, review sections, lessons, release notes, README work, and project documentation.
- `reviewer` - Independent final reviewer for bugs, regressions, missing tests, plan alignment, quality, security, and release readiness.
- `qa-verifier` - QA verification subagent for tests, logs, reproduction, browser or simulator checks, regression risk, and readiness assessment.
- `ai-slops-hunter` - AI-slop detection and cleanup subagent for text, code, UI/design, docs, copy, tests, and generated artifacts.

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
- `ui-ux-designer` - Neural UI/UX design subagent for turning prompts, PRDs, and product constraints into generated screens, flows, prototypes, design specs, and implementation handoff.
- `design-documenter` - Design documentation subagent for creating and updating docs/design/DESIGN.md as the contract between concept, implementation, and verification.
- `pencil-designer` - Pencil MCP design implementation subagent for approved DESIGN.md work in .pen files, variables, layout, screenshots, exports, and visual validation.
- `design-asset-generator` - Visual asset generation subagent for approved DESIGN.md work: hero images, product mockups, illustrations, empty states, icons, and brand visuals.
- `visual-qa` - Visual QA subagent for checking Pencil, Figma, screenshots, or site UI against DESIGN.md: layout, overlap, clipped text, responsiveness, accessibility, and design intent.

## Growth

- `marketing-growth-strategist` - Marketing and growth strategy subagent for business ideas, GTM, positioning, launch, campaigns, SEO, paid ads, growth loops, sales enablement, and promotion strategy.
