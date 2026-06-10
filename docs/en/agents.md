# Agent Flow: agents

This document describes the bundled Agent Flow subagents in English. The working role files in `agents/*.md` are also written in English so the skill stays consistent and portable.

Users do not decide whether to launch subagents. Agent Flow may launch them automatically when the orchestrator decides delegation adds useful evidence or parallelism and the current environment has a tool for launching subagents.

Each `agents/<role>.md` file has runtime `model` and `reasoning_effort` fields plus escalation fields for risky tasks. Before `spawn_agent`, the orchestrator runs `resolve-agent-config.py`, passes any matching `--trigger` values, and uses the selected output as tool arguments.

In large traceable runs, one role can be used several times as separate lanes. For example, `qa-verifier` can separately cover `qa-verifier:admin-rbac`, `qa-verifier:live-feed`, and `qa-verifier:pii`; the distinction lives in `lane-map.json`, handoffs, and trace metadata, not in new role files.

## Core orchestration and planning

### orchestrator

The Agent Flow orchestration support role. It helps the main agent choose the flow, internal budget, trace policy, work sequence, and verification criteria. It prepares delegation packets, integrates handoffs, watches the Definition of Done, and checks that the final response is backed by evidence.

It must not activate Agent Flow by itself or invent public modes. It launches subagents only after internal workflow selection and a usefulness check.

### product-manager

The product agent for turning an idea into a clear task frame. It defines audience, problem, value, MVP scope, constraints, non-goals, assumptions, and acceptance criteria. Use it before architecture or implementation when the request is still too broad or not verifiable.

Output: product frame, acceptance criteria, open questions, and the recommended next role.

### architect

The architecture agent. It turns product scope into an implementation plan: module boundaries, data flow, ownership, change sequence, risks, checks, and rollback concerns. It is useful before multiple worker agents or changes that touch contracts, APIs, database, queues, Kafka, or several subsystems. It also owns the technical review contract for architecture-sensitive code review.

It should not write code instead of worker agents when the plan is already ready.

### researcher

The research agent. It gathers facts from documentation, APIs, SDKs, the repository, GitHub, external sources, and fresh pages. Use it when the model cannot rely on memory: versions, flags, contracts, current rules, competitors, standards, or niche APIs.

Output: research questions, sources, findings, evidence quality, gaps, and recommendation.

### documenter

The documentation agent. It writes PRDs, specifications, README files, release notes, lessons, task docs, and other project documents. Its job is to record decisions, scope, evidence, risks, and next steps in clear language.

It must not invent facts or change canonical documents without explicit scope.

### reviewer

The independent reviewer. It looks for real risks: bugs, regressions, missing checks, plan mismatch, security/data-loss concerns, and release blockers. Use it after implementation or before release. When an architect-owned review contract exists, it checks the diff against that contract before giving a readiness verdict.

Output format: findings by severity, open questions, test gaps, verdict, and residual risks.

### qa-verifier

The QA agent for behavior verification. It runs tests, smoke checks, browser or simulator checks, reproduces scenarios, checks regressions, and assesses readiness.

It does not replace code review. Its job is evidence, not architecture conclusions.

### ai-slops-hunter

The agent for finding and removing AI slop. It checks text, README files, documentation, UI copy, generated code, tests, and public artifacts for templates, empty phrasing, unnecessary comments, generic naming, decorative UI without purpose, and other machine-like patterns.

It does not change product meaning, behavior, APIs, data contracts, or approved design.

## Implementation workers

### backend-worker

The backend executor. It implements bounded changes in server code, APIs, database, queues, auth, integrations, and background jobs from an approved plan.

It must work only inside its ownership, must not change public contracts unless the plan covers that change, and must not run destructive migrations or deploys without permission.

### frontend-worker

The frontend executor. It works with UI, React, styling, responsive behavior, client state, forms, and navigation. Non-trivial visual work needs an approved design source.

It runs build/type/lint/test or browser checks when relevant. It must not redesign the interface without approval.

### typescript-worker

The TypeScript/JavaScript worker. It changes typed app code, Node/Bun/React modules, API clients, shared types, tests, and scoped refactors.

Main focus: type safety, stable contracts, project style, and verifiable behavior.

### bun-worker

The Bun runtime worker. It owns Bun workflow: package scripts, `bun.lock`, dev servers, Bun tests, dependency workflow, and migration from npm/yarn/pnpm to Bun when Bun is the project standard.

It must not mix package managers without an explicit decision.

### python-worker

The Python worker. It handles scoped Python tasks: backend, CLI, automation, data processing, QA scripts, PDF/RAG utilities, tests, and dependency hygiene.

It must preserve simplicity, reproducibility, and clear CLI/API boundaries.

### golang-worker

The Go worker. It implements scoped Go services, Kafka producers/consumers, CLIs, packages, concurrency, tests, modules, and idiomatic refactors.

It must run `gofmt`, relevant `go test`/lint checks, and explicitly describe concurrency or contract decisions.

### ios-worker

The iOS worker. It works with SwiftUI, App Intents, navigation, state, simulator-tested workflows, and Apple-platform constraints.

It must follow existing SwiftUI patterns in the project and, when possible, confirm changes with build/test/simulator evidence.

### rag-retrieval-engineer

The RAG/retrieval engineer. It specializes in retrieval quality: ingestion, chunking, embeddings, vector stores, hybrid search, reranking, graph databases, GraphRAG, citations, freshness, ACL, and evaluation.

Use it when retrieval quality affects grounded LLM responses. Answer generation is secondary; retrieval must be measured and improved first.

## Design flow

### design-orchestrator

The design orchestrator. It routes UI/UX, visual systems, references, Pencil, Figma, Stitch, and `DESIGN.md` tasks. It determines task maturity: raw idea, PRD-ready, direction-ready, approved design, or implementation-blocked.

It watches that frontend/UI implementation does not start without an approved design source.

### ui-reference-researcher

The UI reference researcher. It collects real interface examples, links, screenshots, and pattern-level lessons for landing pages, dashboards, back-office tools, bots, mobile screens, SaaS, game UI, and design systems.

It does not choose the final design and does not recommend copying someone else's solution.

### ui-ux-design-director

The design director. It chooses a UI/UX concept based on the product, audience, references, brand, and implementation constraints. Its job is to choose the direction that works best for the product, not the flashiest option.

Output: accepted concept, rationale, rejected options, UX principles, visual principles, component direction, and accessibility requirements.

### ui-ux-designer

The UI/UX designer for generating and refining screen flows, prototypes, visual direction, design specs, and implementation handoff. It uses neural/design tooling when available and appropriate.

It must not replace approved design or work outside the design-orchestrator route.

### design-documenter

The design documenter. It creates and updates `docs/design/DESIGN.md` as the contract between concept, implementation, and verification. It records status, context, goals, audience, concept, IA, UX flows, visual system, components, states, assets, checks, and risks.

It must not invent approval or leave vague visual statements.

### pencil-designer

The Pencil designer. It implements approved `DESIGN.md` work in a `.pen` artifact: screens, responsive variants, variables, layout, exports, and screenshots.

It does not choose a new design direction. It works only from the approved design source.

### design-asset-generator

The visual asset generator. It prepares hero images, product mockups, illustrations, empty states, icons, and brand visuals from an approved design direction.

It must account for product context, brand constraints, sizes, formats, licensing/source constraints, and integration notes.

### visual-qa

The visual QA role. It checks Pencil/Figma/site UI against `DESIGN.md`: layout, hierarchy, spacing, clipping, overlap, responsiveness, contrast, focus, states, assets, and design intent.

It does not redesign. Its job is to record the mismatch list, severity, screenshots, and fix owners.

## Growth

### marketing-growth-strategist

The marketing and growth agent. It works with GTM, positioning, launch, campaigns, SEO, paid ads, growth loops, sales enablement, ICP, funnel, and lifecycle strategy.

It must not invent market data. If current facts or competitor evidence are needed, it should request research or clearly mark the limitation.
