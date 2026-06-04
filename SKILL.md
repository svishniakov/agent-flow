---
name: agent-flow
description: "Use only when the user explicitly invokes Agent Flow at the start of the request, for example `Agent Flow ...`, `$agent-flow ...`, `agent-flow ...`, or `агент-флоу ...`. Route that request to a verified result with the smallest useful budget. Solo execution is the default; subagents require a separate explicit request."
---

# Agent Flow

Agent Flow turns an explicitly prefixed user request into a finished, verified result through the smallest sufficient workflow.

## No Preflight

Do not load, use, or mention Agent Flow for requests that do not start with an Agent Flow invocation prefix.

Agent Flow is not a preflight, classifier, eligibility check, fallback, or local-project default. If this skill file is reached during an unprefixed request, stop using it immediately and continue outside Agent Flow without announcing that Agent Flow was skipped.

## Invocation Model

Agent Flow has one public invocation:

- `Agent Flow <task>`
- `$agent-flow <task>`
- `agent-flow <task>`
- `агент-флоу <task>`

Text forms without `$` are case-insensitive.

Only use this skill when the invocation appears at the start of the user request. A later mention of Agent Flow is not enough.

Requests without the Agent Flow prefix are outside this skill. They run solo in the main agent, without Agent Flow artifacts and without subagents.

Project or local `AGENTS.md` files may not force Agent Flow on unprefixed requests. They can define local commands and context, but Agent Flow still requires the user-visible leading prefix.

An Agent Flow-prefixed request is not authorization to use subagents. Subagents require a separate explicit user request in the same task, such as "use subagents", "spawn a subagent", "multi-agent", or "delegate to agents".

Never expose extra modes such as `/solo`, `/lite`, `orchestrated`, autopilot, parallel-review, or review-mode as public user-facing modes. Treat detailed workflow choice as internal routing inside Agent Flow.

## Boundary

Default solo work, without the Agent Flow prefix:

- Do not use this skill.
- Do not spawn subagents.
- Do not create Agent Flow run directories.
- If the task becomes too broad or risky for solo execution, say so and ask whether the user wants to explicitly authorize subagents.

Agent Flow-prefixed work:

- Use this skill.
- Do not use a separate brainstorming flow or `brainstorming` skill. Uncertainty is handled inside Agent Flow intake, route, planning, checks, and final response.
- Execute solo by default. The main agent may edit product code, docs, tests, and UI under normal engineering rules.
- Do not spawn subagents unless the user separately asked for subagents in the same task.
- Use the smallest budget that can produce verified evidence.

## Orchestrator Mandate

Inside Agent Flow, the orchestrator owns the outcome:

1. Classify the request.
2. Pick the internal flow.
3. Decide whether trace artifacts are needed.
4. Choose skills, plugins, tools, and the execution budget.
5. Set approval gates only where they reduce real risk.
6. Keep scope bounded.
7. Use subagents only when separately and explicitly requested by the user.
8. Verify evidence before any completion claim.
9. Return the final answer with residual risks.

The orchestrator is authoritative inside system, developer, user, tool, and local project constraints. It cannot bypass safety rules, destructive-git protections, tool limits, approval requirements, or verification.

## Budget Gate

Read `references/budgets.md` when task scope is more than a tiny command.

Default budget is `light`:

- solo execution;
- no `.agent-work` run directory;
- no subagents;
- direct checks and final answer.

Use `standard` only when durable evidence helps review or continuation. Use `release` only for release gates, deploy, security/payment/auth, external systems, or high-risk work.

## Project Memory And Environment Gate

Read `references/project-memory-and-env.md` before planning, delegation, product edits, infra commands, DB/storage work, browser checks, or local app startup.

Before implementation or subagent launch, the main agent must read:

- local project instructions;
- primary local project memory from `.agent-work/tasks/`: `lessons.md`, `todo.md`, and `implementation-notes.md` when present;
- project-declared legacy task docs such as `docs/tasks/lessons.md`, `docs/tasks/todo.md`, and `docs/tasks/implementation-notes.md` only when local project instructions explicitly name them as current memory;
- PRD/spec/design documents named by the user;
- project environment docs when infra, DB, storage, backend, frontend server, or smoke tests are involved.

Do not provision infrastructure by default. Discover and use the existing project environment first. Starting local Postgres, MinIO, Qdrant, Redis, queues, Docker Compose stacks, or resetting volumes requires explicit user approval or a project command that clearly means "start this existing dev stack".

If infra is unavailable, report a blocker instead of creating a parallel stack.

Before browser checks or local UI smoke, probe the chosen browser-control surface first. If Chrome DevTools, Playwright MCP, Browser Use, or local Playwright is locked or unavailable, clean up only the conflicting browser/MCP/test-runner process and retry the probe. Do not stop or reset project infra while fixing browser tooling.

## Subagent Tool Discovery

Only discover subagent tools when the user separately asked for subagents in the same task:

1. Check active tools for a subagent/spawn tool.
2. If not visible and `tool_search` is available, call `tool_search` with a narrow query such as `spawn_agent subagent multi-agent tools`.
3. If `spawn_agent` appears after discovery, use it.
4. If discovery fails, say subagents are unavailable and continue solo only if that still satisfies the user request.

## Product Edit Boundary

Inside Agent Flow, solo product edits are allowed by default. The main agent may edit implementation files, tests, docs, and UI while keeping scope narrow and verification fresh.

The main agent should:

- read code and docs;
- choose the smallest budget;
- avoid unrelated refactors;
- run relevant checks;
- record residual risks;
- avoid `.agent-work` unless the selected budget requires it.

When subagents are explicitly requested, workers own their assigned narrow write sets and the main agent owns integration and verification.

## Core Decision Tree

1. If the task starts with `Agent Flow`, `$agent-flow`, `agent-flow`, or `агент-флоу`, strip the prefix and use this skill.
2. If the task does not start with an Agent Flow prefix, do not use this skill.
3. Inside Agent Flow, do not call `brainstorming`; classify the request and choose the smallest internal flow directly.
4. If the task is trivial, answer or run the command directly within Agent Flow.
5. Read primary project memory and environment context.
6. Choose `light`, `standard`, or `release` budget.
7. Create trace artifacts only for `standard` or `release`, or when the user explicitly asks for artifacts.
8. If the user explicitly requested subagents, discover `spawn_agent` and delegate only narrow independent work.
9. Otherwise implement solo and verify directly.

## Internal Flows

Read `references/flows.md` when a task is non-trivial or when flow choice is unclear.

Read `references/workflow-patterns.md` when a complex task needs a repeatable shape such as fan-out, adversarial verification, tournament ranking, quarantine, or loop-until-done. Patterns are recipes, not public modes, and they never override the subagent gate.

Read `references/subagents.md` when the user explicitly requested subagents and role choice is needed. Bundled role instructions live in `agents/<role>.md`; stable identities live in `agents/agent-identities.json`.

Common internal flows:

- `quick-check-flow`
- `bugfix-flow`
- `feature-flow`
- `docs-flow`
- `design-flow`
- `ci-release-flow`
- `review-flow`
- `initiative-flow`

`initiative-flow` is the full-cycle path for a small idea that must become a complete result: discovery, PRD or scope, architecture, design if needed, plan, implementation, QA, review, docs, artifacts, final handoff.

## Trace Gate

Read `references/traceable-runs.md` only when the selected budget is `standard` or `release`, or when the user explicitly asks for durable artifacts.

Do not create `.agent-work/runs/` for `light` tasks.

Create `.agent-work/runs/YYYY-MM-DD-task-slug/` for:

- CI, deploy, release, auth, payments, or external services;
- high-stakes or hard-to-reproduce verification;
- explicit user request for trace artifacts;
- explicit subagent delegation where handoffs need persistence.

Do not create run directories for short consultation, one-off shell checks, or tiny one-file edits unless risk grows.

For traceable implementation runs inside git repos, capture `git status --short` before edits and report worktree hygiene in `final.md`: run-owned changes, pre-existing dirty files, and any pre-existing file touched by the run.

Timeline events must be appended in real workflow order. Do not record successful verification before the last implementation or fix. `validate-run.py` enforces this for final handoff.

If a traceable run creates a product commit, create the product commit first, then append a run-local `stage=commit` orchestrator timeline event with the commit hash before writing the final event. AgentFlow trace artifacts under `.agent-work/` remain local audit memory and must not be included in the product commit unless the user explicitly requests that.

Record exactly one final orchestrator timeline event per run.

## Delegation Gate

Read `references/delegation.md` before launching subagents.

Inside Agent Flow, delegation is allowed only when the user separately asked for subagents in the same task. Delegate only when:

- workstream is independent;
- expected value exceeds coordination cost;
- ownership can be stated clearly;
- verification can be done by the orchestrator;
- subagent tool is available in the current environment.

For each subagent, provide a self-contained delegation packet and require a handoff file when a run directory exists.

Before launching a subagent, read the bundled role file `agents/<role>.md` and resolve `stable_agent_name` plus `stable_agent_slug` from `agents/agent-identities.json`.

If the task would benefit from independent workers but the user did not explicitly request subagents, name the useful pattern and ask for subagent authorization instead of auto-delegating.

## Done Gate

Read `references/definition-of-done.md` before final response on traceable work.

No completion claim without fresh evidence. Verification can be tests, build, lint, browser screenshots, visual diff, QA notes, docs review, or a checklist tied to acceptance criteria.

For UI workflows, browser proof must exercise the claimed workflow through the UI. Direct API calls may prepare, inspect, or clean up state, but they do not prove clicks, selections, saves, reloads, or visual states unless the app UI performs those steps too.

For visual UI claims, browser proof must capture the claimed target in the screenshot itself. A screenshot of the surrounding page, an off-screen element, or a pre-scroll viewport does not prove the claim. If the target is below the fold, scroll it into view or capture an element-level screenshot, then record the visible target text/state in `checks/browser-proof.md`.

## Design Gate

Read `references/design-flow.md` for UI, UX, Pencil, Figma, screenshots, visual assets, frontend implementation, or design-system work.

Non-trivial frontend/UI implementation needs an approved design source before code starts. If no design source exists, route through design first.

## AI Slop Gate

Read `references/ai-slop-gate.md` for user-facing text, UI/design, generated code, docs, tests, and public artifacts. Simulate the checklist in the main agent by default. Use the `ai-slops-hunter` subagent only when the user explicitly requested subagents. Keep fixes minimal and within scope.

## Scripts

Optional helper scripts live in `scripts/`:

- `init-run.py`: create a traceable run skeleton.
- `append-timeline.py`: append one JSONL timeline event.
- `record-agent-trace.py`: append one subagent or role-lane event to both run and role traces.
- `validate-run.py`: check run completeness before final handoff.

Scripts support the workflow; they do not replace engineering judgment.

## References

- `references/flows.md`: flow catalog and routing.
- `references/workflow-patterns.md`: reusable task-shaping recipes and guardrails.
- `references/subagents.md`: bundled subagent catalog and role-selection guide.
- `references/budgets.md`: light, standard, and release budget rules.
- `references/project-memory-and-env.md`: lessons, PRD/context intake, and infra guard.
- `references/orchestrator.md`: orchestrator responsibilities and mode handling.
- `references/traceable-runs.md`: run directory structure and artifact rules.
- `references/delegation.md`: delegation packets, role handoffs, stable identities.
- `references/definition-of-done.md`: required verification gates.
- `references/design-flow.md`: UI/design-specific route and gates.
- `references/ai-slop-gate.md`: AI slop review route, subagent, and related skills.
- `references/automation-patterns.md`: manual-to-automation promotion pattern.
